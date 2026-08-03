// Bootstraps the test controller without Configuration as Code.
//
// The default image uses CasC, but that plugin is not universally installed --
// the controller this legacy path was written for does not have it -- and
// pinning a CasC version compatible with an older core is another moving part.
// Everything here is Jenkins core, so it works on any 2.x.
import jenkins.model.Jenkins
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import jenkins.security.apitoken.ApiTokenPropertyConfiguration

def instance = Jenkins.get()

def realm = new HudsonPrivateSecurityRealm(false)
realm.createAccount("admin", "admin-test-password")
instance.setSecurityRealm(realm)

def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
instance.setAuthorizationStrategy(strategy)

// The suite mints an API token through the UI endpoint.
def tokenConfig = ApiTokenPropertyConfiguration.get()
tokenConfig.setCreationOfLegacyTokenEnabled(true)
tokenConfig.setTokenGenerationOnCreationEnabled(false)

instance.save()
