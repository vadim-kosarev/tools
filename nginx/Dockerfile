FROM debian:bookworm

#COPY ./etc/apt/sources.list /etc/apt/sources.list

RUN apt update

RUN apt-get install -y hostapd dnsmasq \
    iproute2 iptables iputils-ping net-tools nginx curl python3 python3-pip mc \
    python3-pip python3-uvicorn python3-fastapi iw rfkill wireless-regdb systemctl && \
    apt-get clean

COPY ./etc/hostapd/hostapd.conf /etc/hostapd/hostapd.conf
COPY ./etc/dnsmasq.conf /etc/dnsmasq.conf

COPY ./etc/nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./etc/nginx/sites-available/vaduha_nn.local /etc/nginx/sites-available/vaduha_nn.local
RUN ln -s /etc/nginx/sites-available/vaduha_nn.local /etc/nginx/sites-enabled/vaduha_nn.local && \
    rm /etc/nginx/sites-enabled/default
COPY ./var/www/vkosarev.name /var/www/vkosarev.name

COPY ./entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && \
    chmod +x /entrypoint.sh

WORKDIR /app
CMD ["/entrypoint.sh"]
