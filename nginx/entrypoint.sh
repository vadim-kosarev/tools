#!/bin/bash
set -euo pipefail

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }

if [ "${PROFILE:-}" == "dev" ]; then
  log "DEV mode (PROFILE=dev) - skipping WiFi AP setup"
else
  IFACE=${AP_IFACE:-wlan1}
  IPADDR=${AP_IP:-192.168.50.1}

  if ! ip link show "$IFACE" &>/dev/null; then
    log "Interface $IFACE not found. Existing interfaces:"
    ip link show | awk -F: '/^[0-9]+: /{print $2}'
    exit 1
  fi

  # Разблокировать радио (на случай rfkill)
  if command -v rfkill &>/dev/null; then
    rfkill unblock all || true
  fi

  log "Preparing interface $IFACE for AP mode"
  ip link set "$IFACE" down || true
  # Попытаться установить тип AP (игнорируем ошибку если уже __ap)
  if command -v iw &>/dev/null; then
    iw dev "$IFACE" set type __ap || true
  fi
  ip link set "$IFACE" up

  # Сбросить старые адреса и назначить новый
  ip addr flush dev "$IFACE" || true
  ip addr add "$IPADDR/24" dev "$IFACE"

  # Включить форвардинг IPv4 (может понадобиться для клиентов)
  sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true

  log "Starting dnsmasq"
  dnsmasq --no-daemon --conf-file=/etc/dnsmasq.conf &
  DNSMASQ_PID=$!

  # Небольшая задержка чтобы интерфейс стабилизировался
  sleep 1

  log "Starting hostapd"
  /usr/sbin/hostapd -B /etc/hostapd/hostapd.conf || { \
    log "hostapd failed to start"; \
    kill $DNSMASQ_PID || true; \
    exit 1; }

  # Проверить что интерфейс в UP и имеет IP
  if ! ip addr show dev "$IFACE" | grep -q "$IPADDR"; then
    log "IP $IPADDR not present on $IFACE after configuration";
  fi
fi

log "Starting nginx"
nginx

log "Starting Python API in background"
python3 ./back.py &

# Примитивный watchdog: если hostapd упадет, выводим диагностику (периодический чек)
(
  while true; do
    sleep 30
    if pgrep hostapd >/dev/null; then
      continue
    else
      log "hostapd process not found";
      ip addr show dev "${AP_IFACE:-wlan1}" || true
      break
    fi
  done
) &

# Поддерживать контейнер живым
tail -f /dev/null
