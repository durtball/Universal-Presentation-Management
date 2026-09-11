FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY signage/player/index.html /usr/share/nginx/html/index.html
COPY signage/player/sw.js /usr/share/nginx/html/sw.js
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
