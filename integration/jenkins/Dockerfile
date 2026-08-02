FROM jenkins/jenkins:lts-jdk21
USER root
RUN apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
COPY casc/jenkins.yaml /usr/share/jenkins/ref/casc/jenkins.yaml
COPY entrypoint-tls.sh /usr/local/bin/entrypoint-tls.sh
RUN chmod +x /usr/local/bin/entrypoint-tls.sh
USER jenkins
ENTRYPOINT ["/usr/local/bin/entrypoint-tls.sh"]
