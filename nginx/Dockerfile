FROM debian:bookworm

RUN apt update

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=UTF-8

RUN apt-get install -y hostapd dnsmasq \
    iproute2 iptables iputils-ping net-tools nginx curl python3 python3-pip mc \
    python3-pip python3-uvicorn python3-fastapi iw rfkill wireless-regdb systemctl && \
    apt-get clean

RUN mkdir -p /usr/lib/locale && \
    ln -sf /usr/lib/locale/C.UTF-8 /usr/lib/locale/en_US.utf8 && \
    ln -sf /usr/lib/locale/C.UTF-8 /usr/lib/locale/ru_RU.utf8

#RUN echo "Проверка локалей:" && \
#    python3 -c "import sys; print(f'Кодировка Python: {sys.stdout.encoding}')" && \
#    ls -l /usr/lib/locale/ \

COPY ./etc/hostapd/hostapd.conf /etc/hostapd/hostapd.conf
COPY ./etc/dnsmasq.conf /etc/dnsmasq.conf

COPY ./etc/nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./etc/nginx/sites-available/ /etc/nginx/sites-available/
RUN ln -s /etc/nginx/sites-available/vaduha_nn.local.conf /etc/nginx/sites-enabled/vaduha_nn.local.conf && \
    rm /etc/nginx/sites-enabled/default
COPY ./var/www/vkosarev.name /var/www/vkosarev.name

COPY ./entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && \
    chmod +x /entrypoint.sh

WORKDIR /app
CMD ["/entrypoint.sh"]
