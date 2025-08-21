FROM debian:bookworm

RUN apt-get update && apt-get install -y \
    hostapd \
    dnsmasq \
    iproute2 \
    iptables \
    iputils-ping \
    net-tools \
    nginx \
    python3 python3-pip \
    && apt-get clean

RUN pip3 install fastapi uvicorn

COPY ./etc/hostapd/hostapd.conf /etc/hostapd/hostapd.conf
COPY ./etc/dnsmasq.conf /etc/dnsmasq.conf
COPY ./etc/nginx/nginx.conf /etc/nginx/nginx.conf

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /app
CMD ["/entrypoint.sh"]
