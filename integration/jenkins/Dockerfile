# Jenkins image under test. Overridden by the compatibility matrix workflow to
# check older cores; defaults to the current LTS used by the normal suite.
# Accepted but unused here; only Dockerfile.legacy resolves by line.
ARG JENKINS_UC_LINE=""
ARG JENKINS_IMAGE=jenkins/jenkins:lts-jdk21
FROM ${JENKINS_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
# jenkins-plugin-cli arrived in 2020. Older images ship install-plugins.sh
# instead, and images older still have neither, in which case the build fails
# here rather than producing a Jenkins with no Pipeline support.
# Plain -x tests, not `command -v ... && { ...; }`: under set -e a false
# && chain as the last statement of a loop body aborts the shell, so the
# probe stopped at the first miss and reported nothing found.
RUN set -u; \
    install_with_retry() { \
      attempt=1; \
      while ! "$@"; do \
        if [ "$attempt" -ge 3 ]; then \
          echo "plugin installation failed after $attempt attempts" >&2; \
          return 1; \
        fi; \
        delay=$((attempt * 15)); \
        echo "plugin installation attempt $attempt failed; retrying in $delay seconds" >&2; \
        sleep "$delay"; \
        attempt=$((attempt + 1)); \
      done; \
    }; \
    CLI=""; \
    for c in /bin/jenkins-plugin-cli /usr/local/bin/jenkins-plugin-cli /usr/bin/jenkins-plugin-cli; do \
      if [ -x "$c" ]; then CLI="$c"; break; fi; \
    done; \
    if [ -z "$CLI" ]; then CLI="$(command -v jenkins-plugin-cli || true)"; fi; \
    LEGACY=""; \
    for c in /usr/local/bin/install-plugins.sh /bin/install-plugins.sh; do \
      if [ -x "$c" ]; then LEGACY="$c"; break; fi; \
    done; \
    if [ -n "$CLI" ]; then \
      echo "plugin installer: $CLI"; \
      install_with_retry "$CLI" --plugin-file /usr/share/jenkins/ref/plugins.txt; \
    elif [ -n "$LEGACY" ]; then \
      echo "plugin installer: $LEGACY"; \
      "$LEGACY" $(tr '\n' ' ' < /usr/share/jenkins/ref/plugins.txt); \
    else \
      echo "PLUGIN_INSTALLER_MISSING in this image" >&2; \
      ls -1 /bin /usr/local/bin /usr/bin 2>/dev/null | grep -i plugin >&2 || true; \
      exit 1; \
    fi

COPY casc/jenkins.yaml /usr/share/jenkins/ref/casc/jenkins.yaml
COPY entrypoint-tls.sh /usr/local/bin/entrypoint-tls.sh
RUN chmod +x /usr/local/bin/entrypoint-tls.sh
USER jenkins
ENTRYPOINT ["/usr/local/bin/entrypoint-tls.sh"]
