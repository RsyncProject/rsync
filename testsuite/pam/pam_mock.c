#include "config.h"

#ifdef SUPPORT_PAM

#define PAM_SM_ACCOUNT
#if defined(HAVE_SECURITY_PAM_APPL_H)
#  include <security/pam_appl.h>
#elif defined(HAVE_PAM_PAM_APPL_H)
#  include <pam/pam_appl.h>
#endif

#if defined(HAVE_SECURITY_PAM_MODULES_H)
#  include <security/pam_modules.h>
#elif defined(HAVE_PAM_PAM_MODULES_H)
#  include <pam/pam_modules.h>
#endif
#include <pwd.h>
#include <stddef.h>

/* Handle Solaris vs Linux/macOS pam_get_item signature differences */
#if defined(__sun)
#define PAM_ITEM_OUT_CAST(x) (void **)(x)
#else
#define PAM_ITEM_OUT_CAST(x) (const void **)(x)
#endif

int pam_sm_acct_mgmt(pam_handle_t *pamh, int flags, int argc, const char **argv) {
    const void *user = NULL;
    (void)flags;
    (void)argc;
    (void)argv;

    if (pam_get_item(pamh, PAM_USER, PAM_ITEM_OUT_CAST(&user)) != PAM_SUCCESS || user == NULL) 
        return PAM_PERM_DENIED;
    
    /* Standard logic: success if user exists, unknown if they don't */
    if (getpwnam((const char *)user) != NULL)
        return PAM_SUCCESS;
    
    return PAM_USER_UNKNOWN;
}

#else
/* 
 * If PAM is disabled or headers are missing, we compile an empty file 
 * to prevent compiler errors. */
typedef int make_iso_compilers_happy;
#endif
