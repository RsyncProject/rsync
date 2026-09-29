#ifndef RSYNC_AUTH_H
#define RSYNC_AUTH_H

#ifdef SUPPORT_PAM
/* Verify the user's account status using PAM... */
const char *rsync_pam_validate_account(const char *username);
#endif

void base64_encode(const char *buf, int len, char *out, int pad);
char *auth_server(int f_in, int f_out, int module, const char *host, const char *addr, const char *leader);
void auth_client(int fd, const char *user, const char *challenge);

#endif
