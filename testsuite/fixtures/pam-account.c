#define PAM_SM_ACCOUNT
#include <security/pam_appl.h>
#include <security/pam_modules.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int pam_sm_acct_mgmt(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
	const void *user, *item;
	const struct pam_conv *conversation;
	struct pam_response *response = NULL;
	struct pam_message messages[] = {
		{ PAM_TEXT_INFO, "Account notice" },
		{ PAM_ERROR_MSG, "Account warning" },
	};
	const struct pam_message *message_list[] = { &messages[0], &messages[1] };
	const char *log_path = getenv("RSYNC_TEST_PAM_CALLS");
	FILE *log;
	int status;
	(void)flags;
	(void)argc;
	(void)argv;

	if (!log_path || pam_get_item(pamh, PAM_USER, &user) != PAM_SUCCESS || !user)
		return PAM_SYSTEM_ERR;
	if (!(log = fopen(log_path, "a")))
		return PAM_SYSTEM_ERR;
	fprintf(log, "%s\n", (const char *)user);
	if (fclose(log) != 0)
		return PAM_SYSTEM_ERR;
	if (strcmp(user, "denied") == 0)
		return PAM_PERM_DENIED;
	if (strcmp(user, "expired") == 0)
		return PAM_ACCT_EXPIRED;
	if (pam_get_item(pamh, PAM_CONV, &item) != PAM_SUCCESS || !item)
		return PAM_SYSTEM_ERR;
	conversation = item;
	if (strcmp(user, "interactive") == 0)
		messages[1].msg_style = PAM_PROMPT_ECHO_OFF;
	status = conversation->conv(2, message_list, &response, conversation->appdata_ptr);
	if (status == PAM_SUCCESS && (!response || response[0].resp || response[1].resp))
		status = PAM_SYSTEM_ERR;
	if (response) {
		free(response[0].resp);
		free(response[1].resp);
		free(response);
	}
	return status;
}
