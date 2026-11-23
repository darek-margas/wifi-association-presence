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


@dataclass(frozen=True)
class SpotState:
    kind: SpotKind
    car_id: str | None = None
    height_cm: float | None = None


@dataclass
class SpotTracker:
    config: SpotConfig = field(default_factory=SpotConfig)
    _latched: str | None = None

    def update(
        self,
        occupied: bool | None,
        distance_cm: float | None,
        rssi: dict[str, float | None],
    ) -> SpotState:
        """Evaluate the spot. rssi maps each car's id to its RSSI at this spot."""
        if occupied is None:
            return SpotState(SpotKind.UNKNOWN)
        if not occupied:
            self._latched = None
            return SpotState(SpotKind.EMPTY)

        height = None if distance_cm is None else self.config.floor_cm - distance_cm
        if self._latched is not None:
            # Keep the car while the spot stays occupied: an open tailgate or a
            # weak advert shouldn't move it out.
            return SpotState(SpotKind.CAR, self._latched, height)

        if height is not None and not (
            self.config.min_height_cm <= height <= self.config.max_height_cm
        ):
            return SpotState(SpotKind.OTHER, None, height)

        heard = sorted(
            ((v, k) for k, v in rssi.items() if v is not None and v >= self.config.rssi_min),
            reverse=True,
        )
        if not heard:
            return SpotState(SpotKind.OTHER, None, height)       # a guest car
        if len(heard) > 1 and heard[0][0] - heard[1][0] < self.config.rssi_margin:
            return SpotState(SpotKind.UNSURE, None, height)
        self._latched = heard[0][1]
        return SpotState(SpotKind.CAR, self._latched, height)


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
