FROM debian:bookworm

RUN apt-get update && apt-get install -y hostapd dnsmasq
RUN apt-get install -y iproute2 iptables iputils-ping
RUN apt-get install -y net-tools nginx curl python3 python3-pip
RUN apt-get install -y mc
RUN apt-get clean

RUN apt-get install -y python3-pip python3-uvicorn python3-fastapi

COPY ./etc/hostapd/hostapd.conf /etc/hostapd/hostapd.conf
COPY ./etc/dnsmasq.conf /etc/dnsmasq.conf

COPY ./etc/nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./etc/nginx/sites-available/vaduha_nn.local /etc/nginx/sites-available/vaduha_nn.local
RUN ln -s /etc/nginx/sites-available/vaduha_nn.local /etc/nginx/sites-enabled/vaduha_nn.local
RUN rm /etc/nginx/sites-enabled/default
COPY ./var/www/vkosarev.name /var/www/vkosarev.name

COPY ./entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && \
    chmod +x /entrypoint.sh

WORKDIR /app
CMD ["/entrypoint.sh"]
