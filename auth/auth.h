#ifndef RSYNC_PAM_H
#define RSYNC_PAM_H

/*
 * Verify the user's account status using PAM (pam_acct_mgmt).
 * This ensures the account is not locked, expired, or otherwise restricted
 * by the system administrator's PAM configuration.
 *
 * Returns NULL if access is permitted, or a static error string on failure.
 */
const char *rsync_pam_validate_account(const char *username);

void base64_encode(const char *buf, int len, char *out, int pad);
char *auth_server(int f_in, int f_out, int module, const char *host, const char *addr, const char *leader);
void auth_client(int fd, const char *user, const char *challenge);

#endif

