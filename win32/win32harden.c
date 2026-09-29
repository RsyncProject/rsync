/*
 * Process hardening: the exploit mitigations that have to be switched on by
 * the process itself at startup.  The ones the linker can bake into the image
 * -- CFG, CET, ASLR, DEP, /GS, the Spectre thunks -- are set in CMakeLists.txt
 * instead, and are visible in `dumpbin /headers /loadconfig`.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#include "rsync.h"
#include "win32/win32undef.h"

#ifndef RSYNC_XP_TARGET
typedef BOOL (WINAPI *set_mitigation_fn)(PROCESS_MITIGATION_POLICY, PVOID, SIZE_T);
typedef BOOL (WINAPI *set_dll_dirs_fn)(DWORD);
#endif
typedef BOOL (WINAPI *set_search_path_mode_fn)(DWORD);

void win32_harden(void)
{
	HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
	set_search_path_mode_fn set_search_path_mode;
#ifdef RSYNC_XP_TARGET
#else
	set_mitigation_fn set_policy;
	set_dll_dirs_fn set_dll_dirs;
	PROCESS_MITIGATION_EXTENSION_POINT_DISABLE_POLICY ep;
	PROCESS_MITIGATION_IMAGE_LOAD_POLICY il;
#endif

	if (!k32)
		return;

	set_search_path_mode = (set_search_path_mode_fn)GetProcAddress(
		k32, "SetSearchPathMode");
	if (set_search_path_mode)
		set_search_path_mode(0x00000001 | 0x00008000);

#ifdef RSYNC_XP_TARGET

	HeapSetInformation(NULL, HeapEnableTerminationOnCorruption, NULL, 0);
	return;
#else

	set_policy = (set_mitigation_fn)GetProcAddress(k32, "SetProcessMitigationPolicy");
	set_dll_dirs = (set_dll_dirs_fn)GetProcAddress(k32, "SetDefaultDllDirectories");

	HeapSetInformation(NULL, HeapEnableTerminationOnCorruption, NULL, 0);

	if (set_dll_dirs)
		set_dll_dirs(LOAD_LIBRARY_SEARCH_SYSTEM32);

	if (!set_policy)
		return;

	memset(&ep, 0, sizeof ep);
	ep.DisableExtensionPoints = 1;
	set_policy(ProcessExtensionPointDisablePolicy, &ep, sizeof ep);

	memset(&il, 0, sizeof il);
	il.NoRemoteImages = 1;
	il.NoLowMandatoryLabelImages = 1;
	il.PreferSystem32Images = 1;
	set_policy(ProcessImageLoadPolicy, &il, sizeof il);

	if (RSYNC_STRICT_MITIGATIONS) {
		PROCESS_MITIGATION_DYNAMIC_CODE_POLICY dc;
		PROCESS_MITIGATION_BINARY_SIGNATURE_POLICY sig;
		PROCESS_MITIGATION_STRICT_HANDLE_CHECK_POLICY hc;
		PROCESS_MITIGATION_SIDE_CHANNEL_ISOLATION_POLICY sc;
		PROCESS_MITIGATION_CONTROL_FLOW_GUARD_POLICY cfg;

		memset(&dc, 0, sizeof dc);
		dc.ProhibitDynamicCode = 1;
		set_policy(ProcessDynamicCodePolicy, &dc, sizeof dc);

		memset(&sig, 0, sizeof sig);
		sig.MicrosoftSignedOnly = 1;
		set_policy(ProcessSignaturePolicy, &sig, sizeof sig);

		memset(&hc, 0, sizeof hc);
		hc.RaiseExceptionOnInvalidHandleReference = 1;
		hc.HandleExceptionsPermanentlyEnabled = 1;
		set_policy(ProcessStrictHandleCheckPolicy, &hc, sizeof hc);

		memset(&sc, 0, sizeof sc);
		sc.SmtBranchTargetIsolation = 1;
		sc.SpeculativeStoreBypassDisable = 1;
		sc.IsolateSecurityDomain = 1;
		sc.DisablePageCombine = 1;
		set_policy(ProcessSideChannelIsolationPolicy, &sc, sizeof sc);

		memset(&cfg, 0, sizeof cfg);
		cfg.EnableControlFlowGuard = 1;
		cfg.StrictMode = 1;
		set_policy(ProcessControlFlowGuardPolicy, &cfg, sizeof cfg);
	}
#endif
}
