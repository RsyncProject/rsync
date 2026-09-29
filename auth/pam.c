#include "rsync.h"
#include "auth/auth.h"
#ifdef SUPPORT_PAM

/* Cross-platform PAM header */
#if defined(HAVE_SECURITY_PAM_APPL_H)
#  include <security/pam_appl.h>   /* Linux, recent macOS */
#elif defined(HAVE_PAM_PAM_APPL_H)
#  include <pam/pam_appl.h>        /* UNIX-like */
#else
#  error "PAM is enabled, but no pam_appl.h header was found."
#endif

/* Handle Solaris dropping the const qualifier in pam_message */
#if defined(__sun)
#define PAM_MSG_CONST
#else
#define PAM_MSG_CONST const
#endif

/* 
 * A cross-platform dummy conversation function.
 * Completely eliminates the need for the Linux-only pam_misc.h and misc_conv.
 * If PAM attempts to interactively prompt for a password or display a message,
 * this instantly rejects it to prevent the background daemon from hanging.
 */
static int rsync_pam_conv(int num_msg, PAM_MSG_CONST struct pam_message **msg,
                          struct pam_response **resp, void *appdata_ptr)
{
    /* Suppress unused variable warnings */
    (void)num_msg;
    (void)msg;
    (void)resp;
    (void)appdata_ptr;
    
    return PAM_CONV_ERR; 
}

static struct pam_conv conv = {
    rsync_pam_conv,
    NULL
};

const char *rsync_pam_validate_account(const char *username)
{
    pam_handle_t *pamh = NULL;
    int retval;
    static char pam_err_buf[256];
    const char *final_err = NULL;
    /* 1. Initialize PAM */
    retval = pam_start("rsync", username, &conv, &pamh);
    if (retval != PAM_SUCCESS) {
        snprintf(pam_err_buf, sizeof(pam_err_buf), 
                 "PAM initialization failed for user %s", username);
        return pam_err_buf;
    }
    /* 2. Validate account */
    retval = pam_acct_mgmt(pamh, PAM_SILENT);
    /* 3. Handle result */
    if (retval == PAM_SUCCESS) {
        rprintf(FLOG, "PAM: Account validation successful for user %s\n", username);
    } else {
        snprintf(pam_err_buf, sizeof(pam_err_buf), 
                 "PAM account validation failed, %s", 
                 pam_strerror(pamh, retval));
        final_err = pam_err_buf;
    }
    /* 4. Cleanup */
    pam_end(pamh, retval);
    return final_err;
}

#endif
