#define PAM_SM_ACCOUNT
#include <security/pam_modules.h>
#include <pwd.h>
#include <stddef.h>

int pam_sm_acct_mgmt(pam_handle_t *pamh, int flags, int argc, const char **argv) {
    const char *user;
    if (pam_get_user(pamh, &user, NULL) != PAM_SUCCESS) return PAM_PERM_DENIED;
    if (getpwnam(user) != NULL) return PAM_SUCCESS;
    return PAM_USER_UNKNOWN;
}
