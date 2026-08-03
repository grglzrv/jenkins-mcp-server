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
RUN set -eu; \
    CLI=""; \
    for c in /bin/jenkins-plugin-cli /usr/local/bin/jenkins-plugin-cli \
             /usr/bin/jenkins-plugin-cli jenkins-plugin-cli; do \
      command -v "$c" >/dev/null 2>&1 && { CLI="$c"; break; }; \
    done; \
    LEGACY=""; \
    for c in /usr/local/bin/install-plugins.sh /bin/install-plugins.sh install-plugins.sh; do \
      command -v "$c" >/dev/null 2>&1 && { LEGACY="$c"; break; }; \
    done; \
    if [ -n "$CLI" ]; then \
      echo "using $CLI"; "$CLI" --plugin-file /usr/share/jenkins/ref/plugins.txt; \
    elif [ -n "$LEGACY" ]; then \
      echo "using $LEGACY"; "$LEGACY" $(tr '\n' ' ' < /usr/share/jenkins/ref/plugins.txt); \
    else \
      echo "no plugin installer found. PATH=$PATH" >&2; \
      ls -1 /bin /usr/local/bin 2>/dev/null | sort -u | head -60 >&2; \
      exit 1; \
    fi
COPY casc/jenkins.yaml /usr/share/jenkins/ref/casc/jenkins.yaml
COPY entrypoint-tls.sh /usr/local/bin/entrypoint-tls.sh
RUN chmod +x /usr/local/bin/entrypoint-tls.sh
USER jenkins
ENTRYPOINT ["/usr/local/bin/entrypoint-tls.sh"]
