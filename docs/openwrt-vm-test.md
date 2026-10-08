# Testing the OpenWrt driver in a virtual machine

No Wi-Fi hardware is needed: OpenWrt's `mac80211_hwsim` module creates simulated radios.
One radio runs an access point, a second one connects to it as a client, and hostapd
then lists that client on ubus exactly as on a real AP.

Tested setup to aim for: OpenWrt **24.10.8** x86-64 (current 24.10 release; uses
`opkg`). **25.12** works the same way with `apk` instead of `opkg`. Repeat with **22.03**
for the older `hostapd.wlan0` naming if you like.

## 1. The VM

1. Download the x86-64 image from <https://downloads.openwrt.org/releases/24.10.8/targets/x86/64/>:
   `openwrt-24.10.8-x86-64-generic-ext4-combined.img.gz` (about 13 MB; the BIOS image,
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

     RW <SECTORS> FLAT "openwrt-24.10.8-x86-64-generic-ext4-combined.img" 0

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
3. Give the VM one network adapter **bridged** to your LAN, 256 MB RAM, 1 CPU.

## 2. First boot (VM console)

```sh
passwd                                   # set a root password (needed for SSH)
uci set network.lan.proto='dhcp'         # take an address from your LAN
uci commit network && service network restart
ip -4 addr show br-lan                   # note the address
```

The VM needs internet access for the next step; with `proto='dhcp'` it gets it from your
router.

## 3. Simulated radios, an AP and a client

```sh
opkg update
opkg install kmod-mac80211-hwsim wpad-openssl iw
wifi config                              # writes /etc/config/wireless for the 2 hwsim radios

# radio0: access point "TestAP" (WPA2)
uci set wireless.radio0.disabled='0'
uci set wireless.radio0.band='2g'
uci set wireless.radio0.channel='6'
uci set wireless.default_radio0.ssid='TestAP'
uci set wireless.default_radio0.encryption='psk2'
uci set wireless.default_radio0.key='testpass123'

# radio1: a client that joins TestAP
uci set wireless.radio1.disabled='0'
uci set wireless.radio1.band='2g'
uci set wireless.radio1.channel='6'
uci set wireless.default_radio1.mode='sta'
uci set wireless.default_radio1.network='lan'
uci set wireless.default_radio1.ssid='TestAP'
uci set wireless.default_radio1.encryption='psk2'
uci set wireless.default_radio1.key='testpass123'
uci commit wireless && wifi
```

After a few seconds, check on the VM:

```sh
ubus list 'hostapd.*'                    # e.g. hostapd.phy0-ap0
ubus call hostapd.phy0-ap0 get_clients   # one client, "authorized": true
```

(On OpenWrt 25.x and later, `opkg` is replaced by `apk add …`.)

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
