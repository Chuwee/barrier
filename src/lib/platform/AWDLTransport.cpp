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

#include "platform/AWDLTransport.h"
#include "base/Log.h"

#if defined(__APPLE__)

#include <cstring>
#include <cerrno>
#include <algorithm>
#include <net/if.h>
#include <poll.h>

static const char* kServiceType = "_barrier-p2p._udp";
static const char* kAWDLInterface = "awdl0";

AWDLTransport::AWDLTransport() :
    m_port(0),
    m_registerRef(nullptr),
    m_browseRef(nullptr),
    m_socket(nullptr),
    m_running(false)
{
}

AWDLTransport::~AWDLTransport()
{
    stop();
}

bool
AWDLTransport::start(const std::string& serviceName, UInt16 port)
{
    if (m_running) {
        return true;
    }

    m_serviceName = serviceName;
    m_port = port;

    // Create IPv6 UDP socket for AWDL
    m_socket = new UDPSocket(true);
    if (!m_socket->isValid()) {
        LOG((CLOG_ERR "AWDL: failed to create IPv6 UDP socket"));
        delete m_socket;
        m_socket = nullptr;
        return false;
    }

    // Bind to awdl0 interface
    if (!m_socket->bindToInterface(kAWDLInterface, port)) {
        LOG((CLOG_WARN "AWDL: failed to bind to %s (interface may not exist)",
             kAWDLInterface));
        delete m_socket;
        m_socket = nullptr;
        return false;
    }

    // Register our service with P2P interface to activate AWDL
    DNSServiceErrorType err = DNSServiceRegister(
        &m_registerRef,
        0,                                  // flags
        kDNSServiceInterfaceIndexP2P,       // P2P interface
        serviceName.c_str(),
        kServiceType,
        nullptr,                            // domain (default)
        nullptr,                            // host (default)
        htons(port),
        0, nullptr,                         // no TXT record
        registerCallback,
        this);

    if (err != kDNSServiceErr_NoError) {
        LOG((CLOG_ERR "AWDL: DNSServiceRegister failed: %d", err));
        delete m_socket;
        m_socket = nullptr;
        return false;
    }

    LOG((CLOG_NOTE "AWDL: service registered as \"%s\" on port %d",
         serviceName.c_str(), port));

    // Browse for peers with P2P interface
    err = DNSServiceBrowse(
        &m_browseRef,
        0,                                  // flags
        kDNSServiceInterfaceIndexP2P,       // P2P interface
        kServiceType,
        nullptr,                            // domain (default)
        browseCallback,
        this);

    if (err != kDNSServiceErr_NoError) {
        LOG((CLOG_ERR "AWDL: DNSServiceBrowse failed: %d", err));
        DNSServiceRefDeallocate(m_registerRef);
        m_registerRef = nullptr;
        delete m_socket;
        m_socket = nullptr;
        return false;
    }

    LOG((CLOG_NOTE "AWDL: browsing for peers"));

    // Start event loop thread
    m_running = true;
    m_thread = std::thread(&AWDLTransport::eventLoop, this);

    return true;
}

void
AWDLTransport::stop()
{
    m_running = false;

    if (m_thread.joinable()) {
        m_thread.join();
    }

    // clean up any pending resolve/addrInfo refs
    {
        std::lock_guard<std::mutex> lock(m_pendingMutex);
        for (auto& pr : m_pendingRefs) {
            if (pr.ref) {
                DNSServiceRefDeallocate(pr.ref);
            }
        }
        m_pendingRefs.clear();
    }

    if (m_browseRef) {
        DNSServiceRefDeallocate(m_browseRef);
        m_browseRef = nullptr;
    }

    if (m_registerRef) {
        DNSServiceRefDeallocate(m_registerRef);
        m_registerRef = nullptr;
    }

    delete m_socket;
    m_socket = nullptr;

    {
        std::lock_guard<std::mutex> lock(m_peerMutex);
        m_peerAddrs.clear();
    }

    LOG((CLOG_NOTE "AWDL: transport stopped"));
}

std::vector<P2PTransport::Peer>
AWDLTransport::getPeers() const
{
    std::lock_guard<std::mutex> lock(m_peerMutex);
    std::vector<Peer> result;
    result.reserve(m_peerAddrs.size());
    for (const auto& kv : m_peerAddrs) {
        Peer p;
        p.name = kv.first;
        p.addr = kv.second;
        result.push_back(p);
    }
    return result;
}

bool
AWDLTransport::findPeer(const std::string& name,
                         struct sockaddr_in6& outAddr) const
{
    std::lock_guard<std::mutex> lock(m_peerMutex);
    auto it = m_peerAddrs.find(name);
    if (it != m_peerAddrs.end()) {
        outAddr = it->second;
        return true;
    }
    return false;
}

UDPSocket*
AWDLTransport::getSocket()
{
    return m_socket;
}

bool
AWDLTransport::isActive() const
{
    return m_running && m_socket != nullptr && m_socket->isValid();
}

void
AWDLTransport::addPendingRef(DNSServiceRef ref,
                              std::shared_ptr<ResolveContext> ctx)
{
    std::lock_guard<std::mutex> lock(m_pendingMutex);
    m_pendingRefs.push_back({ref, std::move(ctx)});
}

void
AWDLTransport::cancelPendingForPeer(const std::string& peerName)
{
    std::lock_guard<std::mutex> lock(m_pendingMutex);
    for (auto& pr : m_pendingRefs) {
        if (pr.ctx && pr.ctx->peerName == peerName) {
            pr.ctx->cancelled = true;
            LOG((CLOG_DEBUG "AWDL: cancelled pending ref for removed peer %s",
                 peerName.c_str()));
        }
    }
}

void
AWDLTransport::eventLoop()
{
    while (m_running) {
        // Build pollfd array: register, browse, then any pending refs
        std::vector<struct pollfd> fds;
        std::vector<DNSServiceRef> refs;

        if (m_registerRef) {
            struct pollfd pfd = {};
            pfd.fd = DNSServiceRefSockFD(m_registerRef);
            pfd.events = POLLIN;
            fds.push_back(pfd);
            refs.push_back(m_registerRef);
        }

        if (m_browseRef) {
            struct pollfd pfd = {};
            pfd.fd = DNSServiceRefSockFD(m_browseRef);
            pfd.events = POLLIN;
            fds.push_back(pfd);
            refs.push_back(m_browseRef);
        }

        // snapshot pending refs into the poll set
        {
            std::lock_guard<std::mutex> lock(m_pendingMutex);
            for (auto& pr : m_pendingRefs) {
                if (pr.ref) {
                    struct pollfd pfd = {};
                    pfd.fd = DNSServiceRefSockFD(pr.ref);
                    pfd.events = POLLIN;
                    fds.push_back(pfd);
                    refs.push_back(pr.ref);
                }
            }
        }

        if (fds.empty()) {
            break;
        }

        int ret = poll(fds.data(), static_cast<nfds_t>(fds.size()), 100);
        if (ret < 0) {
            if (errno == EINTR) continue;
            LOG((CLOG_ERR "AWDL: poll failed: %s", std::strerror(errno)));
            break;
        }

        if (ret == 0) continue;

        // process any ready FDs
        for (size_t i = 0; i < fds.size(); ++i) {
            if (fds[i].revents & POLLIN) {
                DNSServiceErrorType err =
                    DNSServiceProcessResult(refs[i]);
                if (err != kDNSServiceErr_NoError) {
                    LOG((CLOG_WARN "AWDL: DNSServiceProcessResult error: %d", err));
                }
            }
        }

        // clean up pending refs that are done or cancelled
        {
            std::lock_guard<std::mutex> lock(m_pendingMutex);
            m_pendingRefs.erase(
                std::remove_if(m_pendingRefs.begin(), m_pendingRefs.end(),
                    [](PendingRef& pr) {
                        if (pr.ctx && (pr.ctx->done || pr.ctx->cancelled)) {
                            if (pr.ref) {
                                DNSServiceRefDeallocate(pr.ref);
                                pr.ref = nullptr;
                            }
                            return true;
                        }
                        return false;
                    }),
                m_pendingRefs.end());
        }
    }
}

void DNSSD_API
AWDLTransport::registerCallback(
    DNSServiceRef,
    DNSServiceFlags,
    DNSServiceErrorType errorCode,
    const char* name,
    const char* regtype,
    const char*,
    void*)
{
    if (errorCode == kDNSServiceErr_NoError) {
        LOG((CLOG_NOTE "AWDL: service registered: %s %s", name, regtype));
    }
    else {
        LOG((CLOG_ERR "AWDL: registration failed: %d", errorCode));
    }
}

void DNSSD_API
AWDLTransport::browseCallback(
    DNSServiceRef,
    DNSServiceFlags flags,
    uint32_t interfaceIndex,
    DNSServiceErrorType errorCode,
    const char* serviceName,
    const char* regtype,
    const char* replyDomain,
    void* context)
{
    AWDLTransport* self = static_cast<AWDLTransport*>(context);

    if (errorCode != kDNSServiceErr_NoError) {
        LOG((CLOG_ERR "AWDL: browse error: %d", errorCode));
        return;
    }

    if (!(flags & kDNSServiceFlagsAdd)) {
        // peer removed — cancel any in-flight resolve/addrInfo for this peer
        LOG((CLOG_NOTE "AWDL: peer removed: %s", serviceName));
        self->cancelPendingForPeer(serviceName);
        {
            std::lock_guard<std::mutex> lock(self->m_peerMutex);
            self->m_peerAddrs.erase(serviceName);
        }
        return;
    }

    // skip our own service
    if (self->m_serviceName == serviceName) {
        LOG((CLOG_DEBUG "AWDL: ignoring own service: %s", serviceName));
        return;
    }

    LOG((CLOG_NOTE "AWDL: peer discovered: %s on interface %u",
         serviceName, interfaceIndex));

    // Start async resolve — the ref will be polled in the event loop
    auto rctx = std::make_shared<ResolveContext>();
    rctx->self = self;
    rctx->peerName = serviceName;
    rctx->ifIndex = interfaceIndex;
    rctx->done = false;
    rctx->cancelled = false;

    DNSServiceRef resolveRef = nullptr;
    DNSServiceErrorType err = DNSServiceResolve(
        &resolveRef,
        0,
        interfaceIndex,
        serviceName,
        regtype,
        replyDomain,
        resolveCallback,
        rctx.get());

    if (err != kDNSServiceErr_NoError) {
        LOG((CLOG_ERR "AWDL: DNSServiceResolve failed: %d", err));
        return;
    }

    // hand the ref and context to the event loop for async processing
    self->addPendingRef(resolveRef, rctx);
}

void DNSSD_API
AWDLTransport::resolveCallback(
    DNSServiceRef,
    DNSServiceFlags flags,
    uint32_t interfaceIndex,
    DNSServiceErrorType errorCode,
    const char*,
    const char* hosttarget,
    uint16_t port,
    uint16_t,
    const unsigned char*,
    void* context)
{
    ResolveContext* rctx = static_cast<ResolveContext*>(context);

    if (rctx->cancelled) {
        rctx->done = true;
        return;
    }

    if (errorCode != kDNSServiceErr_NoError) {
        LOG((CLOG_ERR "AWDL: resolve failed for %s: %d",
             rctx->peerName.c_str(), errorCode));
        rctx->done = true;
        return;
    }

    LOG((CLOG_DEBUG "AWDL: resolved %s -> %s:%d on if %u",
         rctx->peerName.c_str(), hosttarget, ntohs(port), interfaceIndex));

    // Mark this resolve ref as done (resolve is single-result)
    rctx->done = true;

    // Start async address lookup — new ref polled in the event loop
    auto actx = std::make_shared<ResolveContext>();
    actx->self = rctx->self;
    actx->peerName = rctx->peerName;
    actx->ifIndex = interfaceIndex;
    actx->done = false;
    actx->cancelled = false;

    DNSServiceRef addrRef = nullptr;
    DNSServiceErrorType err = DNSServiceGetAddrInfo(
        &addrRef,
        0,
        interfaceIndex,
        kDNSServiceProtocol_IPv6,
        hosttarget,
        addrInfoCallback,
        actx.get());

    if (err != kDNSServiceErr_NoError) {
        LOG((CLOG_ERR "AWDL: DNSServiceGetAddrInfo failed: %d", err));
        return;
    }

    // hand to event loop for async processing
    rctx->self->addPendingRef(addrRef, actx);
}

void DNSSD_API
AWDLTransport::addrInfoCallback(
    DNSServiceRef,
    DNSServiceFlags flags,
    uint32_t interfaceIndex,
    DNSServiceErrorType errorCode,
    const char* hostname,
    const struct sockaddr* address,
    uint32_t,
    void* context)
{
    ResolveContext* rctx = static_cast<ResolveContext*>(context);

    // mark done when no more results are coming
    if (!(flags & kDNSServiceFlagsMoreComing)) {
        rctx->done = true;
    }

    if (rctx->cancelled) {
        return;
    }

    if (errorCode != kDNSServiceErr_NoError || address == nullptr) {
        LOG((CLOG_ERR "AWDL: getAddrInfo failed for %s: %d",
             rctx->peerName.c_str(), errorCode));
        return;
    }

    if (address->sa_family != AF_INET6) {
        LOG((CLOG_DEBUG "AWDL: skipping non-IPv6 address for %s", hostname));
        return;
    }

    const struct sockaddr_in6* addr6 =
        reinterpret_cast<const struct sockaddr_in6*>(address);

    // Build peer address
    struct sockaddr_in6 peerAddr;
    std::memcpy(&peerAddr, addr6, sizeof(peerAddr));
    // Ensure scope_id is set for the AWDL interface
    peerAddr.sin6_scope_id = interfaceIndex;
    // Use our P2P port (the resolved port is the remote service port)
    peerAddr.sin6_port = htons(rctx->self->m_port);

    char addrStr[INET6_ADDRSTRLEN];
    inet_ntop(AF_INET6, &addr6->sin6_addr, addrStr, sizeof(addrStr));
    LOG((CLOG_NOTE "AWDL: peer %s at [%s%%if%u]:%d",
         rctx->peerName.c_str(), addrStr, interfaceIndex, rctx->self->m_port));

    // Add/update peer (O(log N) map insert/update)
    {
        std::lock_guard<std::mutex> lock(rctx->self->m_peerMutex);
        rctx->self->m_peerAddrs[rctx->peerName] = peerAddr;
    }
}

#endif // __APPLE__
