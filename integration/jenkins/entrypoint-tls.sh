#!/usr/bin/env bash
set -euo pipefail
mkdir -p /var/jenkins_home/certs
if [[ ! -f /var/jenkins_home/certs/jenkins.p12 ]]; then
  openssl req -x509 -newkey rsa:2048 -sha256 -days 2 -nodes -subj '/CN=jenkins' -addext 'subjectAltName=DNS:jenkins,DNS:localhost' -keyout /var/jenkins_home/certs/key.pem -out /var/jenkins_home/certs/ca.crt
  openssl pkcs12 -export -out /var/jenkins_home/certs/jenkins.p12 -inkey /var/jenkins_home/certs/key.pem -in /var/jenkins_home/certs/ca.crt -passout pass:changeit
fi
exec /usr/bin/tini -- /usr/local/bin/jenkins.sh --httpPort=-1 --httpsPort=8443 --httpsKeyStore=/var/jenkins_home/certs/jenkins.p12 --httpsKeyStorePassword=changeit
