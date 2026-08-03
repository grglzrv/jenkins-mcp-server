# Jenkins image under test. Overridden by the compatibility matrix workflow to
# check older cores; defaults to the current LTS used by the normal suite.
ARG JENKINS_IMAGE=jenkins/jenkins:lts-jdk21
FROM ${JENKINS_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
# jenkins-plugin-cli arrived in 2020. Older images ship install-plugins.sh
# instead, and images older still have neither, in which case the build fails
# here rather than producing a Jenkins with no Pipeline support.
RUN if command -v jenkins-plugin-cli >/dev/null 2>&1; then \
      jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt; \
    elif command -v install-plugins.sh >/dev/null 2>&1; then \
      install-plugins.sh $(tr '\n' ' ' < /usr/share/jenkins/ref/plugins.txt); \
    else \
      echo "no plugin installer in this image; cannot provision plugins" >&2; exit 1; \
    fi
COPY casc/jenkins.yaml /usr/share/jenkins/ref/casc/jenkins.yaml
COPY entrypoint-tls.sh /usr/local/bin/entrypoint-tls.sh
RUN chmod +x /usr/local/bin/entrypoint-tls.sh
USER jenkins
ENTRYPOINT ["/usr/local/bin/entrypoint-tls.sh"]
