/*
 * barrier -- mouse and keyboard sharing utility
 *
 * This package is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * found in the file LICENSE that should have accompanied this file.
 *
 * This package is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#pragma once

#include "base/EventTypes.h"

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
typedef SOCKET UDPSocketFD;
#define UDP_INVALID_SOCKET INVALID_SOCKET
#else
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <unistd.h>
#include <fcntl.h>
#include <net/if.h>
typedef int UDPSocketFD;
#define UDP_INVALID_SOCKET (-1)
#endif

//! Simple UDP socket wrapper
/*!
A minimal UDP socket for sending and receiving fixed-size datagrams.
Uses raw POSIX sockets (or Winsock on Windows). Non-blocking.
Supports both IPv4 and IPv6 (for P2P/AWDL transport).
*/
class UDPSocket {
public:
    //! Create a UDP socket
    /*!
    If \p ipv6 is true, creates an AF_INET6 socket instead of AF_INET.
    */
    UDPSocket(bool ipv6 = false);
    ~UDPSocket();

    //! Bind to a local port (for server)
    /*!
    Binds the socket to the given port on all interfaces.
    Returns true on success.
    */
    bool                bind(UInt16 port);

    //! Bind to a specific interface and port (IPv6)
    /*!
    Binds the socket to a named interface (e.g. "awdl0") on the
    given port. Sets sin6_scope_id from the interface name.
    Returns true on success.
    */
    bool                bindToInterface(const char* ifname, UInt16 port);

    //! Set the remote address (for client, IPv4)
    /*!
    Sets the default destination for send(). The address is
    specified as a dotted-quad or hostname string and port.
    Returns true on success.
    */
    bool                setTarget(const char* host, UInt16 port);

    //! Set the remote IPv6 address (for client)
    /*!
    Sets the default destination for send() using an IPv6 sockaddr.
    */
    void                setTargetIPv6(const struct sockaddr_in6& addr);

    //! Send datagram to the configured target
    /*!
    Sends \p size bytes from \p data to the target set by setTarget().
    Returns the number of bytes sent, or -1 on error.
    */
    int                 send(const void* data, int size);

    //! Send datagram to a specific IPv4 address
    int                 sendTo(const void* data, int size,
                            const struct sockaddr_in& addr);

    //! Send datagram to a specific IPv6 address
    int                 sendToIPv6(const void* data, int size,
                            const struct sockaddr_in6& addr);

    //! Receive a datagram (non-blocking, IPv4)
    /*!
    Reads up to \p maxSize bytes into \p buffer. If \p fromAddr is
    not NULL, the sender's address is stored there.
    Returns the number of bytes read, or 0 if no data available,
    or -1 on error.
    */
    int                 recv(void* buffer, int maxSize,
                            struct sockaddr_in* fromAddr = nullptr);

    //! Receive a datagram (non-blocking, IPv6)
    int                 recvIPv6(void* buffer, int maxSize,
                            struct sockaddr_in6* fromAddr = nullptr);

    //! Check if socket is valid
    bool                isValid() const { return m_fd != UDP_INVALID_SOCKET; }

    //! Check if this is an IPv6 socket
    bool                isIPv6() const { return m_ipv6; }

    //! Get the raw file descriptor
    UDPSocketFD         getFD() const { return m_fd; }

private:
    void                createSocket();
    void                setNonBlocking();
    void                closeSocket();

    // generic sendto/recvfrom that handle both address families
    int                 sendToAddr(const void* data, int size,
                            const struct sockaddr* addr, socklen_t addrLen);
    int                 recvFromAddr(void* buffer, int maxSize,
                            struct sockaddr* fromAddr, socklen_t* fromLen);

    UDPSocketFD         m_fd;
    bool                m_ipv6;
    struct sockaddr_storage m_target;
    bool                m_hasTarget;
};
