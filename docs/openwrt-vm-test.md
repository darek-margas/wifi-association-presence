# Testing the OpenWrt driver in a virtual machine

No Wi-Fi hardware is needed: OpenWrt's `mac80211_hwsim` module creates simulated radios.
One radio runs an access point, a second one connects to it as a client, and hostapd
then lists that client on ubus exactly as on a real AP.

Tested setup: OpenWrt **25.12.5** x86-64, the current stable release, in VMware (2026-10-08):
the driver read the simulated client with SSID, band and signal, and the leave / return
test worked. Its output is kept as a test fixture
(`tests/fixtures/openwrt-25.12.5-x86-hwsim.txt`). The previous series, **24.10**,
works the same way with `opkg` instead of `apk`. Repeat with **22.03** for the older
`hostapd.wlan0` naming if you like.

## 1. The VM

1. Download the x86-64 image from <https://downloads.openwrt.org/releases/25.12.5/targets/x86/64/>:
   `openwrt-25.12.5-x86-64-generic-ext4-combined.img.gz` (about 13 MB; the BIOS image,
   simpler than `-efi`), and unpack it (7-Zip, or `gunzip`; a "trailing garbage" warning
   is normal).
2. Use it in your hypervisor:
   - **VMware Workstation / Player**: there is no official VMware image, but VMware can
     use the raw image as a "flat" disk. Next to the `.img`, create `openwrt.vmdk` with:
     ```text
     # Disk DescriptorFile
     version=1
     CID=fffffffe
     parentCID=ffffffff
     createType="monolithicFlat"

     RW <SECTORS> FLAT "openwrt-25.12.5-x86-64-generic-ext4-combined.img" 0

     ddb.adapterType = "ide"
     ddb.geometry.heads = "16"
     ddb.geometry.sectors = "63"
     ddb.geometry.cylinders = "<CYLINDERS>"
     ddb.virtualHWVersion = "4"
     ```
     where `<SECTORS>` is the `.img` file size in bytes divided by 512, and
     `<CYLINDERS>` is `<SECTORS>` / 1008, rounded up. Then **New Virtual Machine →
     Custom → I will install the operating system later → Linux, Other Linux 5.x kernel
     64-bit**, 256 MB RAM, network **Bridged**, and at the disk step **Use an existing
     virtual disk** → `openwrt.vmdk` (keep the existing format if asked). For ESXi, convert
     with `vmkfstools -i openwrt.vmdk openwrt-esxi.vmdk` on the host instead.
   - VirtualBox: `VBoxManage convertfromraw openwrt-…-combined.img openwrt.vdi --format VDI`
   - Hyper-V: `qemu-img convert -O vhdx openwrt-…-combined.img openwrt.vhdx`
     (Generation 1 VM, Secure Boot off)
   - Proxmox / QEMU: use the `.img` as is.
   - **QNAP Virtualization Station** (KVM): copy the `.img` to a shared folder and create a
     VM with it as an **existing disk image** (Linux / Other, BIOS firmware, disk VirtIO or
     SATA, network VirtIO or e1000 on a virtual switch bridged to your LAN). No conversion.
3. Give the VM one network adapter **bridged** to your LAN, 256 MB RAM, 1 CPU.

## 2. First boot (VM console)

**Do this before anything else.** A fresh OpenWrt takes **192.168.1.1** on its LAN port
and runs a **DHCP server** there. Bridged to your network, that can clash with your router
(which is often 192.168.1.1 too) and hand out wrong addresses to other devices.

```sh
passwd                                   # set a root password (needed for SSH)
uci set network.lan.proto='dhcp'         # become a normal DHCP client on your LAN
uci delete network.lan.ipaddr
uci delete network.lan.netmask           # "Entry not found" is fine
uci set dhcp.lan.ignore='1'              # no DHCP server on your LAN
uci commit
service dnsmasq restart
service network restart
sleep 10; ip -4 addr show br-lan         # note the address (run again if still empty)
```

The VM needs internet access for the next step; with `proto='dhcp'` it gets it from your
router.

## 3. Simulated radios, an AP and a client

Install the simulated radios and the Wi-Fi daemon, then **reboot**: the x86 image boots
without any Wi-Fi, and the daemon only registers on ubus cleanly after a boot with it
installed.

```sh
apk update
apk add kmod-mac80211-hwsim wpad-basic-mbedtls iw
# (24.10 and older: opkg update && opkg install kmod-mac80211-hwsim wpad-basic-mbedtls iw)
reboot
```

After the reboot, configure an AP and a client. Both get their own network that is **not
bridged to your LAN**, so nothing of the test reaches your real network:

```sh
wifi config                              # writes /etc/config/wireless for the 2 hwsim radios

uci set network.testap=interface         # isolated network for the test AP
uci set network.testap.proto='static'
uci set network.testap.ipaddr='10.99.0.1'
uci set network.testap.netmask='255.255.255.0'
uci set network.teststa=interface        # the client needs no address
uci set network.teststa.proto='none'

for r in 0 1; do
  uci set wireless.radio$r.disabled='0'
  uci set wireless.radio$r.band='2g'
  uci set wireless.radio$r.channel='6'
  uci set wireless.radio$r.htmode='HT20'
  uci set wireless.radio$r.country='AU'  # your country: the default '00' is refused for an AP
  uci set wireless.default_radio$r.disabled='0'   # 25.12 also disables the interfaces
  uci set wireless.default_radio$r.ssid='TestAP'
  uci set wireless.default_radio$r.encryption='psk2'
  uci set wireless.default_radio$r.key='testpass123'
done
uci set wireless.default_radio0.mode='ap'          # radio0: access point "TestAP"
uci set wireless.default_radio0.network='testap'
uci set wireless.default_radio1.mode='sta'         # radio1: a client that joins it
uci set wireless.default_radio1.network='teststa'
uci commit network && uci commit wireless
service network reload && wifi
```

After about 20 seconds, check on the VM:

```sh
ubus list 'hostapd.*'                    # hostapd.phy0-ap0
iw dev phy1-sta0 link                    # "Connected to ... SSID: TestAP"
ubus call hostapd.phy0-ap0 get_clients   # one client, "authorized": true
```

If `hostapd.phy0-ap0` is missing, `logread | grep hostapd` says why (an invalid
`country_code` was the usual cause).

## 4. The driver against the VM

From the repository on your computer (`pip install asyncssh`):

```sh
python3 scripts/probe.py --type openwrt_ssh --host <VM address> --username root
```

Expected: `1 associated client(s)`, band `2.4GHz`, a signal in dBm and SSID `TestAP`.
Then add it in Home Assistant as **Add access point → OpenWrt (SSH, ubus)** with the same
address and password, and track the client's MAC.

Things worth trying:

- `wifi down radio1`: the client disappears from the next poll and the tracker goes
  away after the grace period.
- `wifi up radio1`: it comes back, with a new `arrived_at`.
- A second AP interface on radio0 (another SSID) to see two hostapd objects.

Report the result in the issues, including the OpenWrt version.
