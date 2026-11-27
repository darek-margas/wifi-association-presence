"""Which car is in which parking spot, and which cars are home but outside.

Pure logic, no Home Assistant imports, so it can be unit tested on its own
(tests/test_parking.py). The integration feeds it entity states.

The same file is in tesla-ble-fleet and wifi-association-presence: change both.
Cars are keyed by an opaque id: the fleet uses Car IDs, Wi-Fi Association
Presence the subentry ids of its tracked devices.

A spot is an occupancy sensor over one parking space (e.g. an ultrasonic
sensor in the ceiling). Two questions are answered separately:
- Is the thing in the spot a car? From its height: floor distance minus the
  measured distance. A ladder, bin or person gives a height outside the band.
  Both cars here are Model Ys a few cm apart, so height doesn't say which.
- Which car? From the Bluetooth advert RSSI of each car at the spot's ESP32:
  the strongest, if it is strong enough and clearly ahead of the next one.

Once a car is identified the spot keeps it until the spot is empty again. A
parked car doesn't change, and RSSI wobbles: the on-device "Parked Car" sensor
flipped between a name and Unknown every few seconds.

Two guards around that, both optional so older callers keep working:
- A reading that goes missing is held for rssi_hold_s (pass now). Without it a
  car driving in while its advert was missed lost to a weaker car on the
  driveway, and the wrong car stayed latched.
- The latch is dropped when the latched car is away and unheard while another
  is clearly at the spot (pass present). The occupancy sensor's off delay can
  outlast a quick swap of cars, and a latch restored after a restart may be
  from before the cars were swapped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SpotKind(StrEnum):
    UNKNOWN = "unknown"      # occupancy sensor unavailable
    EMPTY = "empty"
    CAR = "car"              # a known Tesla, see SpotState.car_id
    UNSURE = "unsure"        # car-shaped, two Teslas too close in RSSI to tell
    OTHER = "other"          # occupied, but not one of our Teslas


class Location(StrEnum):
    AWAY = "away"
    IN_SPOT = "in_spot"
    OUTSIDE = "outside"
    HOME = "home"            # home, but which spot or outside can't be told


@dataclass(frozen=True)
class SpotConfig:
    floor_cm: float = 280.0
    min_height_cm: float = 135.0      # Model 3 ~144 cm
    max_height_cm: float = 195.0      # Model Y L ~167 cm, Model X ~168 cm
    rssi_min: float = -85.0           # weaker than this: car not near the spot
    rssi_margin: float = 6.0          # best must beat the second by this much
    rssi_hold_s: float = 30.0         # a missing reading keeps its last value this long


@dataclass(frozen=True)
class SpotState:
    kind: SpotKind
    car_id: str | None = None
    height_cm: float | None = None


@dataclass
class SpotTracker:
    config: SpotConfig = field(default_factory=SpotConfig)
    _latched: str | None = None
    _heard: dict[str, tuple[float, float]] = field(default_factory=dict)   # car -> (rssi, when)

    @property
    def latched(self) -> str | None:
        """The identified car, to save across restarts."""
        return self._latched

    def restore(self, car_id: str | None) -> None:
        """Put back a saved latch. It is dropped again if the spot turns out empty."""
        self._latched = car_id

    def update(
        self,
        occupied: bool | None,
        distance_cm: float | None,
        rssi: dict[str, float | None],
        present: dict[str, bool | None] | None = None,
        now: float | None = None,
    ) -> SpotState:
        """Evaluate the spot.

        rssi maps each car's id to its RSSI at this spot. present maps each
        car's id to whether it is home; now is a monotonic time in seconds.
        """
        rssi = self._held(rssi, now)
        if occupied is None:
            return SpotState(SpotKind.UNKNOWN)
        if not occupied:
            self._latched = None
            return SpotState(SpotKind.EMPTY)

        height = None if distance_cm is None else self.config.floor_cm - distance_cm
        heard = sorted(
            ((v, k) for k, v in rssi.items() if v is not None and v >= self.config.rssi_min),
            reverse=True,
        )
        clear = bool(heard) and (
            len(heard) == 1 or heard[0][0] - heard[1][0] >= self.config.rssi_margin
        )
        if self._latched is not None:
            swapped = (
                present is not None
                and present.get(self._latched) is False
                and clear
                and all(car_id != self._latched for _, car_id in heard)
            )
            if not swapped:
                # Keep the car while the spot stays occupied: an open tailgate or a
                # weak advert shouldn't move it out.
                return SpotState(SpotKind.CAR, self._latched, height)
            self._latched = None

        if height is not None and not (
            self.config.min_height_cm <= height <= self.config.max_height_cm
        ):
            return SpotState(SpotKind.OTHER, None, height)
        if not heard:
            return SpotState(SpotKind.OTHER, None, height)       # a guest car
        if not clear:
            return SpotState(SpotKind.UNSURE, None, height)
        self._latched = heard[0][1]
        return SpotState(SpotKind.CAR, self._latched, height)

    def _held(self, rssi: dict[str, float | None], now: float | None) -> dict[str, float | None]:
        """Fill a missing reading with the car's last one, if recent enough."""
        if now is None:
            return rssi
        out: dict[str, float | None] = {}
        for car_id, value in rssi.items():
            if value is not None:
                self._heard[car_id] = (value, now)
            else:
                last = self._heard.get(car_id)
                if last is not None and now - last[1] <= self.config.rssi_hold_s:
                    value = last[0]
            out[car_id] = value
        return out


@dataclass(frozen=True)
class CarPlace:
    location: Location | None       # None: no data about the car at all
    spot: str | None = None


def locate_cars(
    present: dict[str, bool | None],
    spots: dict[str, SpotState],
) -> dict[str, CarPlace]:
    """Place each car. present maps each car's id to whether it is home (None: unknown)."""
    in_spot = {s.car_id: name for name, s in spots.items() if s.kind is SpotKind.CAR}
    unclear = any(s.kind in (SpotKind.UNKNOWN, SpotKind.UNSURE) for s in spots.values())
    places: dict[str, CarPlace] = {}
    for car_id, here in present.items():
        if car_id in in_spot:
            # Identified in a spot by its own advert: home even if the presence
            # source lags behind (or a sleeping car has left the Wi-Fi).
            places[car_id] = CarPlace(Location.IN_SPOT, in_spot[car_id])
        elif here is None:
            places[car_id] = CarPlace(None)
        elif not here:
            places[car_id] = CarPlace(Location.AWAY)
        elif unclear:
            places[car_id] = CarPlace(Location.HOME)
        else:
            places[car_id] = CarPlace(Location.OUTSIDE)
    return places


def summary(places: dict[str, CarPlace]) -> str | None:
    """'2 in', '1 in, 1 out', '2 out', '1 out, 1 away', ..."""
    order = [(Location.IN_SPOT, "in"), (Location.OUTSIDE, "out"),
             (Location.HOME, "home"), (Location.AWAY, "away")]
    counts = {loc: 0 for loc, _ in order}
    for p in places.values():
        if p.location is None:
            return None
        counts[p.location] += 1
    parts = [f"{counts[loc]} {word}" for loc, word in order if counts[loc]]
    return ", ".join(parts) if parts else None
