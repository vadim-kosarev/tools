#!/bin/bash
set -e

if [ "$PROFILE" == "dev" ]; then
  echo "[*] Running in DEV mode (PROFILE=dev) - skipping WiFi AP setup"
else
  # IP для AP-устройства
  IFACE=${AP_IFACE:-wlan1}
  IPADDR=${AP_IP:-192.168.50.1}

  echo "[*] Configuring $IFACE with IP $IPADDR"
  ip link set $IFACE up
  ip addr add $IPADDR/24 dev $IFACE || true

  echo "[*] Starting dnsmasq"
  dnsmasq --no-daemon --conf-file=/etc/dnsmasq.conf &

  echo "[*] Starting hostapd"
  /usr/sbin/hostapd -B /etc/hostapd/hostapd.conf
fi

echo "[*] Starting nginx"
nginx

echo "[*] Starting Python API in background"
python3 ./back.py &

tail -f /dev/null
