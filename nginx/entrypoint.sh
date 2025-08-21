#!/bin/bash
set -e

# IP для AP-интерфейса
IFACE=${AP_IFACE:-wlan1}
IPADDR=${AP_IP:-192.168.50.1}

echo "[*] Configuring $IFACE with IP $IPADDR"
ip link set $IFACE up
ip addr add $IPADDR/24 dev $IFACE || true

echo "[*] Starting dnsmasq"
dnsmasq --no-daemon --conf-file=/etc/dnsmasq.conf &

echo "[*] Starting hostapd"
/usr/sbin/hostapd -B /etc/hostapd/hostapd.conf

echo "[*] Starting nginx"
nginx

echo "[*] Starting Python API"
/usr/local/bin/uvicorn main:app --host 0.0.0.0 --port 8000
