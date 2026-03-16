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
#include <unistd.h>
#include <fcntl.h>
typedef int UDPSocketFD;
#define UDP_INVALID_SOCKET (-1)
#endif

//! Simple UDP socket wrapper
/*!
A minimal UDP socket for sending and receiving fixed-size datagrams.
Uses raw POSIX sockets (or Winsock on Windows). Non-blocking.
*/
class UDPSocket {
public:
    UDPSocket();
    ~UDPSocket();

    //! Bind to a local port (for server)
    /*!
    Binds the socket to the given port on all interfaces.
    Returns true on success.
    */
    bool                bind(UInt16 port);

    //! Set the remote address (for client)
    /*!
    Sets the default destination for send(). The address is
    specified as a dotted-quad or hostname string and port.
    Returns true on success.
    */
    bool                setTarget(const char* host, UInt16 port);

    //! Send datagram to the configured target
    /*!
    Sends \p size bytes from \p data to the target set by setTarget().
    Returns the number of bytes sent, or -1 on error.
    */
    int                 send(const void* data, int size);

    //! Send datagram to a specific address
    /*!
    Sends \p size bytes from \p data to the given sockaddr.
    Returns the number of bytes sent, or -1 on error.
    */
    int                 sendTo(const void* data, int size,
                            const struct sockaddr_in& addr);

    //! Receive a datagram (non-blocking)
    /*!
    Reads up to \p maxSize bytes into \p buffer. If \p fromAddr is
    not NULL, the sender's address is stored there.
    Returns the number of bytes read, or 0 if no data available,
    or -1 on error.
    */
    int                 recv(void* buffer, int maxSize,
                            struct sockaddr_in* fromAddr = nullptr);

    //! Check if socket is valid
    bool                isValid() const { return m_fd != UDP_INVALID_SOCKET; }

    //! Get the raw file descriptor
    UDPSocketFD         getFD() const { return m_fd; }

private:
    void                createSocket();
    void                setNonBlocking();
    void                closeSocket();

    UDPSocketFD         m_fd;
    struct sockaddr_in  m_target;
    bool                m_hasTarget;
};
