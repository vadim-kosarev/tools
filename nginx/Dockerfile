FROM debian:bookworm

# Set APT options early (IPv4 + retries)
RUN printf 'Acquire::Retries "5";\nAcquire::ForceIPv4 "true";\n' > /etc/apt/apt.conf.d/99force-ipv4

# Copy cleaned sources.list (separate deb / deb-src lines). Keep minimal repos to avoid slow mirrors.
COPY ./etc/apt/sources.list /etc/apt/sources.list

# Single apt layer to reduce network overhead
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      hostapd dnsmasq iproute2 iptables iputils-ping net-tools nginx curl \
      python3 python3-pip mc && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Extra python packages (if really needed) - uvicorn & fastapi via pip (smaller than apt)
RUN pip3 install --no-cache-dir uvicorn fastapi

COPY ./etc/hostapd/hostapd.conf /etc/hostapd/hostapd.conf
COPY ./etc/dnsmasq.conf /etc/dnsmasq.conf

COPY ./etc/nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./etc/nginx/sites-available/vaduha_nn.local /etc/nginx/sites-available/vaduha_nn.local
RUN ln -s /etc/nginx/sites-available/vaduha_nn.local /etc/nginx/sites-enabled/vaduha_nn.local && \
    rm /etc/nginx/sites-enabled/default
COPY ./var/www/vkosarev.name /var/www/vkosarev.name

COPY ./entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

WORKDIR /app
CMD ["/entrypoint.sh"]
