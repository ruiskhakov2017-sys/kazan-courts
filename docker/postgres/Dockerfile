FROM postgres:18.6-alpine
# Bind-mounted init scripts can be unreadable to postgres after a private checkout.
COPY --chmod=0755 docker/postgres/init.sh /docker-entrypoint-initdb.d/01-init.sh
