#!/bin/sh
# vim: noet
# The "noet" is important because of the -EOF behavior.
opt() {
	name=$1
	level=$2
	optname=$3
	optval=$4
	opttype=$5

	check_optval=1
	case ${optval} in
		[A-Z_]*) ;;
		*) check_optval='' ;;
	esac
	# If we have configure --disable-ipv6, the options might be available in
	# the system headers, but the builder wants to NOT support them.
	optdef_extra=''
	case $name in
		IPV6*) optdef_extra=' && defined(INET6)' ;;
	esac

	# If the values are NOT available at compile-time, we should recognize the
	# input and emit an error.
	cat <<-EOF
	#if defined(${level}) && defined(${optname}) ${check_optval:+&& defined(${optval})}${optdef_extra}
	  {"${name}", ${level}, ${optname}, ${optval}, ${opttype}},
	#else
	  {"${name}", SOCK_OPT_ERR, SOCK_OPT_ERR, SOCK_OPT_ERR, ${opttype}},
	#endif
	EOF
}

opt_bool() {
	opt "$1" "$2" "${3:-$1}" 0 SOCK_OPT_BOOL
}
opt_int() {
	opt "$1" "$2" "${3:-$1}" 0 SOCK_OPT_INT
}
opt_val() {
	opt "$1" "$2" "$3" "$4" SOCK_OPT_ON
}
opt_str() {
	opt "$1" "$2" "${3:-$1}" 0 SOCK_OPT_STR
}

cat <<-EOF
#include "rsync.h"
#ifdef HAVE_NETINET_IN_SYSTM_H
#include <netinet/in_systm.h>
#endif
#ifdef HAVE_NETINET_IP_H
#include <netinet/ip.h>
#endif
#ifdef HAVE_NETINET_IP6_H
#include <netinet/ip6.h>
#endif
#include <netinet/tcp.h>
struct socket_option socket_options[] = {
EOF

# grouped by level, sorted by name.
opt_str		SO_BINDTODEVICE			SOL_SOCKET
opt_bool	SO_BROADCAST			SOL_SOCKET
opt_int		SO_BUSY_POLL			SOL_SOCKET
opt_bool	SO_DEBUG				SOL_SOCKET
opt_bool	SO_DONTROUTE			SOL_SOCKET
opt_int		SO_INCOMING_CPU			SOL_SOCKET
opt_bool	SO_KEEPALIVE			SOL_SOCKET
opt_int		SO_MARK					SOL_SOCKET
opt_int		SO_PRIORITY				SOL_SOCKET
opt_int		SO_RCVBUF				SOL_SOCKET
opt_int		SO_RCVLOWAT				SOL_SOCKET
opt_int		SO_RCVTIMEO				SOL_SOCKET
opt_bool	SO_REUSEADDR			SOL_SOCKET
opt_bool	SO_REUSEPORT			SOL_SOCKET
opt_int		SO_SNDBUF				SOL_SOCKET
opt_int		SO_SNDLOWAT				SOL_SOCKET
opt_int		SO_SNDTIMEO				SOL_SOCKET

opt_bool	IP_BIND_ADDRESS_NO_PORT IPPROTO_IP
opt_int		IP_CHECKSUM				IPPROTO_IP
opt_bool	IP_FREEBIND				IPPROTO_IP
opt_int		IP_LOCAL_PORT_RANGE		IPPROTO_IP
opt_int		IP_MINTTL				IPPROTO_IP
opt_int		IP_MTU					IPPROTO_IP
opt_val		IP_PMTUDISC_DO			IPPROTO_IP	IP_MTU_DISCOVER	IP_PMTUDISC_DO
opt_val		IP_PMTUDISC_DONT		IPPROTO_IP	IP_MTU_DISCOVER	IP_PMTUDISC_DONT
opt_val		IP_PMTUDISC_INTERFACE	IPPROTO_IP	IP_MTU_DISCOVER	IP_PMTUDISC_INTERFACE
opt_val		IP_PMTUDISC_OMIT		IPPROTO_IP	IP_MTU_DISCOVER	IP_PMTUDISC_OMIT
opt_val		IP_PMTUDISC_PROBE		IPPROTO_IP	IP_MTU_DISCOVER	IP_PMTUDISC_PROBE
opt_val		IP_PMTUDISC_WANT		IPPROTO_IP	IP_MTU_DISCOVER	IP_PMTUDISC_WANT
opt_bool	IP_TRANSPARENT			IPPROTO_IP

# sorting exception, to group the IPTOS together.
opt_val		IPTOS_LOWDELAY			IPPROTO_IP	IP_TOS	IPTOS_LOWDELAY
opt_val		IPTOS_MINCOST			IPPROTO_IP	IP_TOS	IPTOS_MINCOST
opt_val		IPTOS_RELIABILITY		IPPROTO_IP	IP_TOS	IPTOS_RELIABILITY
opt_val		IPTOS_THROUGHPUT		IPPROTO_IP	IP_TOS	IPTOS_THROUGHPUT

opt_bool	IPV6_FREEBIND			IPPROTO_IPV6
opt_int		IPV6_MTU				IPPROTO_IPV6
opt_val		IPV6_PMTUDISC_DO		IPPROTO_IPV6	IPV6_MTU_DISCOVER	IPV6_PMTUDISC_DO
opt_val		IPV6_PMTUDISC_DONT		IPPROTO_IPV6	IPV6_MTU_DISCOVER	IPV6_PMTUDISC_DONT
opt_val		IPV6_PMTUDISC_INTERFACE	IPPROTO_IPV6	IPV6_MTU_DISCOVER	IPV6_PMTUDISC_INTERFACE
opt_val		IPV6_PMTUDISC_OMIT		IPPROTO_IPV6	IPV6_MTU_DISCOVER	IPV6_PMTUDISC_OMIT
opt_val		IPV6_PMTUDISC_PROBE		IPPROTO_IPV6	IPV6_MTU_DISCOVER	IPV6_PMTUDISC_PROBE
opt_val		IPV6_PMTUDISC_WANT		IPPROTO_IPV6	IPV6_MTU_DISCOVER	IPV6_PMTUDISC_WANT
opt_bool	IPV6_TRANSPARENT		IPPROTO_IPV6
opt_int		IPV6_UNICAST_HOPS		IPPROTO_IPV6

opt_str		TCP_CONGESTION			IPPROTO_TCP
opt_bool	TCP_CORK				IPPROTO_TCP
opt_int		TCP_DEFER_ACCEPT		IPPROTO_TCP
opt_bool	TCP_FASTOPEN_CONNECT	IPPROTO_TCP
opt_bool	TCP_FASTOPEN			IPPROTO_TCP
opt_int		TCP_KEEPCNT				IPPROTO_TCP
opt_int		TCP_KEEPIDLE			IPPROTO_TCP
opt_int		TCP_KEEPINTVL			IPPROTO_TCP
opt_int		TCP_LINGER2				IPPROTO_TCP
opt_int		TCP_MAXSEG				IPPROTO_TCP
opt_bool	TCP_NODELAY				IPPROTO_TCP
opt_bool	TCP_QUICKACK			IPPROTO_TCP
opt_int		TCP_SYNCNT				IPPROTO_TCP
opt_int		TCP_USER_TIMEOUT		IPPROTO_TCP
opt_int		TCP_WINDOW_CLAMP		IPPROTO_TCP

cat <<EOF
  {NULL, 0, 0, 0, 0}
};
EOF
