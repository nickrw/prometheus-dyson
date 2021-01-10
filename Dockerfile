FROM python:3
RUN pip3 install libpurecool prometheus_client
WORKDIR /app
VOLUME ["/config"]
ENTRYPOINT ["/usr/local/bin/python3", "main.py", "--port", "9034", "--config", "/config/config.ini"]
EXPOSE 9034/tcp
COPY main.py metrics.py /app/
