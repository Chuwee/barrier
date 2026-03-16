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

#include "net/UDPSocket.h"
#include "base/Log.h"

#include <cstring>
#include <cerrno>

#ifdef _WIN32
#pragma comment(lib, "ws2_32.lib")
#endif

// macOS-specific: required for AWDL socket traffic
#if defined(__APPLE__)
#ifndef SO_RECV_ANYIF
#define SO_RECV_ANYIF 0x1104
#endif
#endif

UDPSocket::UDPSocket(bool ipv6) :
    m_fd(UDP_INVALID_SOCKET),
    m_ipv6(ipv6),
    m_hasTarget(false)
{
    std::memset(&m_target, 0, sizeof(m_target));
    createSocket();
    if (m_fd != UDP_INVALID_SOCKET) {
        setNonBlocking();
    }
}

UDPSocket::~UDPSocket()
{
    closeSocket();
}

void
UDPSocket::createSocket()
{
    if (m_ipv6) {
        m_fd = ::socket(AF_INET6, SOCK_DGRAM, IPPROTO_UDP);
    }
    else {
        m_fd = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    }

    if (m_fd == UDP_INVALID_SOCKET) {
        LOG((CLOG_ERR "failed to create UDP%s socket: %s",
             m_ipv6 ? "v6" : "", std::strerror(errno)));
        return;
    }

#if defined(__APPLE__)
    if (m_ipv6) {
        // SO_RECV_ANYIF is required on macOS for AWDL interface traffic
        int opt = 1;
        if (setsockopt(m_fd, SOL_SOCKET, SO_RECV_ANYIF, &opt, sizeof(opt)) != 0) {
            LOG((CLOG_WARN "failed to set SO_RECV_ANYIF: %s", std::strerror(errno)));
        }
    }
#endif
}

void
UDPSocket::setNonBlocking()
{
    if (m_fd == UDP_INVALID_SOCKET) {
        return;
    }

#ifdef _WIN32
    u_long mode = 1;
    if (ioctlsocket(m_fd, FIONBIO, &mode) != 0) {
        LOG((CLOG_ERR "failed to set UDP socket non-blocking"));
    }
#else
    int flags = fcntl(m_fd, F_GETFL, 0);
    if (flags == -1) {
        LOG((CLOG_ERR "failed to get UDP socket flags: %s", std::strerror(errno)));
        return;
    }
    if (fcntl(m_fd, F_SETFL, flags | O_NONBLOCK) == -1) {
        LOG((CLOG_ERR "failed to set UDP socket non-blocking: %s", std::strerror(errno)));
    }
#endif
}

void
UDPSocket::closeSocket()
{
    if (m_fd != UDP_INVALID_SOCKET) {
#ifdef _WIN32
        closesocket(m_fd);
#else
        ::close(m_fd);
#endif
        m_fd = UDP_INVALID_SOCKET;
    }
}

bool
UDPSocket::bind(UInt16 port)
{
    if (m_fd == UDP_INVALID_SOCKET) {
        return false;
    }

    // allow address reuse
    int opt = 1;
#ifdef _WIN32
    setsockopt(m_fd, SOL_SOCKET, SO_REUSEADDR,
               reinterpret_cast<const char*>(&opt), sizeof(opt));
#else
    setsockopt(m_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
#endif

    if (m_ipv6) {
        struct sockaddr_in6 addr;
        std::memset(&addr, 0, sizeof(addr));
        addr.sin6_family = AF_INET6;
        addr.sin6_addr   = in6addr_any;
        addr.sin6_port   = htons(port);

        if (::bind(m_fd, reinterpret_cast<struct sockaddr*>(&addr),
                   sizeof(addr)) != 0) {
            LOG((CLOG_ERR "failed to bind UDPv6 socket to port %d: %s",
                 port, std::strerror(errno)));
            return false;
        }
        LOG((CLOG_DEBUG "UDPv6 socket bound to port %d", port));
    }
    else {
        struct sockaddr_in addr;
        std::memset(&addr, 0, sizeof(addr));
        addr.sin_family      = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_ANY);
        addr.sin_port        = htons(port);

        if (::bind(m_fd, reinterpret_cast<struct sockaddr*>(&addr),
                   sizeof(addr)) != 0) {
            LOG((CLOG_ERR "failed to bind UDP socket to port %d: %s",
                 port, std::strerror(errno)));
            return false;
        }
        LOG((CLOG_DEBUG "UDP socket bound to port %d", port));
    }

    return true;
}

bool
UDPSocket::bindToInterface(const char* ifname, UInt16 port)
{
#ifndef _WIN32
    if (m_fd == UDP_INVALID_SOCKET || !m_ipv6) {
        return false;
    }

    // allow address reuse
    int opt = 1;
    setsockopt(m_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    unsigned int ifindex = if_nametoindex(ifname);
    if (ifindex == 0) {
        LOG((CLOG_ERR "interface \"%s\" not found: %s", ifname, std::strerror(errno)));
        return false;
    }

    struct sockaddr_in6 addr;
    std::memset(&addr, 0, sizeof(addr));
    addr.sin6_family   = AF_INET6;
    addr.sin6_addr     = in6addr_any;
    addr.sin6_port     = htons(port);
    addr.sin6_scope_id = ifindex;

    if (::bind(m_fd, reinterpret_cast<struct sockaddr*>(&addr),
               sizeof(addr)) != 0) {
        LOG((CLOG_ERR "failed to bind UDPv6 socket to %s port %d: %s",
             ifname, port, std::strerror(errno)));
        return false;
    }

    LOG((CLOG_DEBUG "UDPv6 socket bound to %s port %d (scope_id=%u)",
         ifname, port, ifindex));
    return true;
#else
    (void)ifname;
    (void)port;
    return false;
#endif
}

bool
UDPSocket::setTarget(const char* host, UInt16 port)
{
    if (m_fd == UDP_INVALID_SOCKET) {
        return false;
    }

    std::memset(&m_target, 0, sizeof(m_target));
    struct sockaddr_in* target4 = reinterpret_cast<struct sockaddr_in*>(&m_target);
    target4->sin_family = AF_INET;
    target4->sin_port   = htons(port);

    // try as numeric address first, fall back to DNS resolution
    if (inet_pton(AF_INET, host, &target4->sin_addr) != 1) {
        struct addrinfo hints, *res = NULL;
        std::memset(&hints, 0, sizeof(hints));
        hints.ai_family = AF_INET;
        hints.ai_socktype = SOCK_DGRAM;
        if (getaddrinfo(host, NULL, &hints, &res) != 0 || res == NULL) {
            LOG((CLOG_ERR "failed to resolve UDP target: %s", host));
            m_hasTarget = false;
            return false;
        }
        target4->sin_addr =
            reinterpret_cast<struct sockaddr_in*>(res->ai_addr)->sin_addr;
        freeaddrinfo(res);
    }

    m_hasTarget = true;
    LOG((CLOG_DEBUG "UDP target set to %s:%d", host, port));
    return true;
}

void
UDPSocket::setTargetIPv6(const struct sockaddr_in6& addr)
{
    std::memset(&m_target, 0, sizeof(m_target));
    std::memcpy(&m_target, &addr, sizeof(addr));
    m_hasTarget = true;
}

int
UDPSocket::send(const void* data, int size)
{
    if (m_fd == UDP_INVALID_SOCKET || !m_hasTarget) {
        return -1;
    }

    if (m_ipv6) {
        return sendToIPv6(data, size,
            *reinterpret_cast<const struct sockaddr_in6*>(&m_target));
    }
    return sendTo(data, size,
        *reinterpret_cast<const struct sockaddr_in*>(&m_target));
}

// generic sendto wrapper — shared by sendTo() and sendToIPv6()
int
UDPSocket::sendToAddr(const void* data, int size,
                       const struct sockaddr* addr, socklen_t addrLen)
{
    if (m_fd == UDP_INVALID_SOCKET) {
        return -1;
    }

#ifdef _WIN32
    int n = ::sendto(m_fd, static_cast<const char*>(data), size, 0,
                     addr, addrLen);
#else
    ssize_t n = ::sendto(m_fd, data, size, 0, addr, addrLen);
#endif

    if (n < 0) {
#ifdef _WIN32
        int err = WSAGetLastError();
        if (err != WSAEWOULDBLOCK) {
            LOG((CLOG_DEBUG2 "UDP sendto failed: error %d", err));
        }
#else
        if (errno != EAGAIN && errno != EWOULDBLOCK) {
            LOG((CLOG_DEBUG2 "UDP sendto failed: %s", std::strerror(errno)));
        }
#endif
        return -1;
    }

    return static_cast<int>(n);
}

int
UDPSocket::sendTo(const void* data, int size,
                   const struct sockaddr_in& addr)
{
    return sendToAddr(data, size,
        reinterpret_cast<const struct sockaddr*>(&addr), sizeof(addr));
}

int
UDPSocket::sendToIPv6(const void* data, int size,
                       const struct sockaddr_in6& addr)
{
    return sendToAddr(data, size,
        reinterpret_cast<const struct sockaddr*>(&addr), sizeof(addr));
}

// generic recvfrom wrapper — shared by recv() and recvIPv6()
int
UDPSocket::recvFromAddr(void* buffer, int maxSize,
                         struct sockaddr* fromAddr, socklen_t* fromLen)
{
    if (m_fd == UDP_INVALID_SOCKET) {
        return -1;
    }

    struct sockaddr_storage sender;
    socklen_t senderLen = sizeof(sender);
    std::memset(&sender, 0, sizeof(sender));

#ifdef _WIN32
    int n = ::recvfrom(m_fd, static_cast<char*>(buffer), maxSize, 0,
                       reinterpret_cast<struct sockaddr*>(&sender),
                       &senderLen);
#else
    ssize_t n = ::recvfrom(m_fd, buffer, maxSize, 0,
                           reinterpret_cast<struct sockaddr*>(&sender),
                           &senderLen);
#endif

    if (n < 0) {
#ifdef _WIN32
        int err = WSAGetLastError();
        if (err == WSAEWOULDBLOCK) {
            return 0;
        }
        LOG((CLOG_DEBUG2 "UDP recvfrom failed: error %d", err));
#else
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return 0;
        }
        LOG((CLOG_DEBUG2 "UDP recvfrom failed: %s", std::strerror(errno)));
#endif
        return -1;
    }

    if (fromAddr != nullptr && fromLen != nullptr) {
        socklen_t copyLen = (senderLen < *fromLen) ? senderLen : *fromLen;
        std::memcpy(fromAddr, &sender, copyLen);
        *fromLen = senderLen;
    }

    return static_cast<int>(n);
}

int
UDPSocket::recv(void* buffer, int maxSize, struct sockaddr_in* fromAddr)
{
    if (fromAddr) {
        socklen_t len = sizeof(*fromAddr);
        std::memset(fromAddr, 0, len);
        return recvFromAddr(buffer, maxSize,
            reinterpret_cast<struct sockaddr*>(fromAddr), &len);
    }
    return recvFromAddr(buffer, maxSize, nullptr, nullptr);
}

int
UDPSocket::recvIPv6(void* buffer, int maxSize, struct sockaddr_in6* fromAddr)
{
    if (fromAddr) {
        socklen_t len = sizeof(*fromAddr);
        std::memset(fromAddr, 0, len);
        return recvFromAddr(buffer, maxSize,
            reinterpret_cast<struct sockaddr*>(fromAddr), &len);
    }
    return recvFromAddr(buffer, maxSize, nullptr, nullptr);
}
