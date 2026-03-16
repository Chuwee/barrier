/*
 * barrier -- mouse and keyboard sharing utility
 * Copyright (C) 2012-2016 Symless Ltd.
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

#include "DisplayLayoutWidget.h"
#include "ServerConfig.h"

#include <QGuiApplication>
#include <QMouseEvent>
#include <QPainter>
#include <QScreen>
#include <algorithm>
#include <cmath>

DisplayLayoutWidget::DisplayLayoutWidget(QWidget* parent)
    : QWidget(parent)
    , m_pServerConfig(nullptr)
    , m_dragIndex(-1)
    , m_resizing(false)
    , m_resizeCorner(0)
    , m_scale(1.0)
{
    setMinimumSize(400, 250);
    setMouseTracking(true);
}

void DisplayLayoutWidget::setServerName(const QString& name)
{
    m_serverName = name;
    rebuildLocalMonitors();
    update();
}

void DisplayLayoutWidget::setRemoteScreens(const QStringList& screenNames)
{
    // remove existing remotes
    for (int i = m_displays.size() - 1; i >= 0; --i) {
        if (!m_displays[i].isLocal)
            m_displays.removeAt(i);
    }

    // add new remotes
    bool anyRestored = false;
    for (const QString& name : screenNames) {
        DisplayRect d;
        d.name = name;
        d.monitorIndex = -1;
        d.isLocal = false;
        d.rect = QRectF(0, 0, DEFAULT_REMOTE_W, DEFAULT_REMOTE_H);

        // restore saved position and size if available
        if (m_pServerConfig && m_pServerConfig->displayPositions().contains(name)) {
            QPointF pos = m_pServerConfig->displayPositions().value(name);
            d.rect.moveTopLeft(pos);
            if (m_pServerConfig->displaySizes().contains(name)) {
                QSizeF sz = m_pServerConfig->displaySizes().value(name);
                d.rect.setSize(QSizeF(sz.width(), sz.height()));
            }
            anyRestored = true;
        }

        m_displays.append(d);
    }

    // only auto-layout if no saved positions were found
    if (!anyRestored)
        layoutRemoteScreens();

    update();
}

void DisplayLayoutWidget::rebuildLocalMonitors()
{
    // remove existing locals
    for (int i = m_displays.size() - 1; i >= 0; --i) {
        if (m_displays[i].isLocal)
            m_displays.removeAt(i);
    }

    QList<QScreen*> screens = QGuiApplication::screens();
    if (screens.isEmpty())
        return;

    // sort left-to-right, then top-to-bottom
    QList<QScreen*> sorted = screens;
    std::sort(sorted.begin(), sorted.end(), [](QScreen* a, QScreen* b) {
        if (a->geometry().x() != b->geometry().x())
            return a->geometry().x() < b->geometry().x();
        return a->geometry().y() < b->geometry().y();
    });

    for (int i = 0; i < sorted.size(); ++i) {
        QScreen* s = sorted[i];
        QRect g = s->geometry();
        DisplayRect d;
        d.name = m_serverName;
        d.monitorIndex = i;
        d.isLocal = true;
        d.rect = QRectF(g.x(), g.y(), g.width(), g.height());
        m_displays.append(d);
    }
}

void DisplayLayoutWidget::layoutRemoteScreens()
{
    // find bounding box of all local monitors
    QRectF localBounds;
    for (const DisplayRect& d : m_displays) {
        if (d.isLocal) {
            if (localBounds.isNull())
                localBounds = d.rect;
            else
                localBounds = localBounds.united(d.rect);
        }
    }

    if (localBounds.isNull())
        localBounds = QRectF(0, 0, 1920, 1080);

    // place remote screens alternating left and right of locals
    qreal leftX = localBounds.left() - DEFAULT_REMOTE_W - 50;
    qreal rightX = localBounds.right() + 50;
    bool placeRight = true;

    for (DisplayRect& d : m_displays) {
        if (d.isLocal)
            continue;

        qreal cy = localBounds.center().y() - d.rect.height() / 2.0;
        if (placeRight) {
            d.rect.moveTopLeft(QPointF(rightX, cy));
            rightX += d.rect.width() + 50;
        } else {
            d.rect.moveTopLeft(QPointF(leftX - d.rect.width(), cy));
            leftX -= d.rect.width() + 50;
        }
        placeRight = !placeRight;
    }
}

// --- coordinate transforms ---

void DisplayLayoutWidget::updateTransform()
{
    if (m_displays.isEmpty()) {
        m_scale = 1.0;
        m_viewOffset = QPointF(0, 0);
        return;
    }

    QRectF bounds;
    for (const DisplayRect& d : m_displays) {
        if (bounds.isNull())
            bounds = d.rect;
        else
            bounds = bounds.united(d.rect);
    }

    // add padding
    qreal pad = bounds.width() * 0.1;
    bounds.adjust(-pad, -pad, pad, pad);

    qreal sx = width() / bounds.width();
    qreal sy = height() / bounds.height();
    m_scale = qMin(sx, sy);
    m_viewOffset = QPointF(
        (width() - bounds.width() * m_scale) / 2.0 - bounds.left() * m_scale,
        (height() - bounds.height() * m_scale) / 2.0 - bounds.top() * m_scale
    );
}

QRectF DisplayLayoutWidget::toWidget(const QRectF& r) const
{
    return QRectF(
        r.x() * m_scale + m_viewOffset.x(),
        r.y() * m_scale + m_viewOffset.y(),
        r.width() * m_scale,
        r.height() * m_scale
    );
}

QPointF DisplayLayoutWidget::toLogical(const QPointF& p) const
{
    return QPointF(
        (p.x() - m_viewOffset.x()) / m_scale,
        (p.y() - m_viewOffset.y()) / m_scale
    );
}

// --- hit testing ---

int DisplayLayoutWidget::hitTest(const QPointF& widgetPos) const
{
    // prefer remotes (they're on top and draggable)
    for (int i = m_displays.size() - 1; i >= 0; --i) {
        if (toWidget(m_displays[i].rect).contains(widgetPos))
            return i;
    }
    return -1;
}

// --- snapping ---

void DisplayLayoutWidget::snapToEdges(int dragIndex)
{
    DisplayRect& d = m_displays[dragIndex];
    qreal bestDx = SNAP_DIST + 1, bestDy = SNAP_DIST + 1;
    qreal snapDx = 0, snapDy = 0;

    for (int i = 0; i < m_displays.size(); ++i) {
        if (i == dragIndex)
            continue;
        const QRectF& o = m_displays[i].rect;

        // check vertical overlap for left/right snapping
        bool vOverlap = d.rect.bottom() > o.top() + 10 && d.rect.top() < o.bottom() - 10;
        // check horizontal overlap for top/bottom snapping
        bool hOverlap = d.rect.right() > o.left() + 10 && d.rect.left() < o.right() - 10;

        if (vOverlap) {
            // snap right edge of d to left edge of o
            qreal dx = o.left() - d.rect.right();
            if (std::abs(dx) < std::abs(bestDx)) {
                bestDx = dx;
                snapDx = dx;
            }
            // snap left edge of d to right edge of o
            dx = o.right() - d.rect.left();
            if (std::abs(dx) < std::abs(bestDx)) {
                bestDx = dx;
                snapDx = dx;
            }
        }

        if (hOverlap) {
            // snap bottom edge of d to top edge of o
            qreal dy = o.top() - d.rect.bottom();
            if (std::abs(dy) < std::abs(bestDy)) {
                bestDy = dy;
                snapDy = dy;
            }
            // snap top edge of d to bottom edge of o
            dy = o.bottom() - d.rect.top();
            if (std::abs(dy) < std::abs(bestDy)) {
                bestDy = dy;
                snapDy = dy;
            }
        }

        // also snap tops/bottoms to align
        qreal dy = o.top() - d.rect.top();
        if (std::abs(dy) < std::abs(bestDy) && std::abs(dy) < SNAP_DIST) {
            bestDy = dy;
            snapDy = dy;
        }
        dy = o.bottom() - d.rect.bottom();
        if (std::abs(dy) < std::abs(bestDy) && std::abs(dy) < SNAP_DIST) {
            bestDy = dy;
            snapDy = dy;
        }
        dy = o.center().y() - d.rect.center().y();
        if (std::abs(dy) < std::abs(bestDy) && std::abs(dy) < SNAP_DIST) {
            bestDy = dy;
            snapDy = dy;
        }
    }

    if (std::abs(bestDx) <= SNAP_DIST)
        d.rect.translate(snapDx, 0);
    if (std::abs(bestDy) <= SNAP_DIST)
        d.rect.translate(0, snapDy);
}

// --- adjacency / link generation ---

QList<GeneratedLink> DisplayLayoutWidget::computeLinks() const
{
    QList<GeneratedLink> links;

    // helper to add a link if not already present
    auto addLink = [&links](const GeneratedLink& link) {
        for (const GeneratedLink& existing : links) {
            if (existing.srcScreen == link.srcScreen &&
                existing.dstScreen == link.dstScreen &&
                existing.direction == link.direction &&
                existing.srcMonitorIndex == link.srcMonitorIndex)
                return;
        }
        links.append(link);
    };

    for (int i = 0; i < m_displays.size(); ++i) {
        for (int j = 0; j < m_displays.size(); ++j) {
            if (i == j) continue;

            const DisplayRect& a = m_displays[i];
            const DisplayRect& b = m_displays[j];

            // skip local-to-local on same machine (macOS handles natively)
            if (a.isLocal && b.isLocal && a.name == b.name)
                continue;

            // right edge of a touching left edge of b → a:right=b, b:left=a
            if (std::abs(a.rect.right() - b.rect.left()) < ADJ_DIST) {
                qreal overlapTop = qMax(a.rect.top(), b.rect.top());
                qreal overlapBot = qMin(a.rect.bottom(), b.rect.bottom());
                if (overlapBot - overlapTop > 10) {
                    GeneratedLink fwd;
                    fwd.srcScreen = a.name;
                    fwd.dstScreen = b.name;
                    fwd.direction = "right";
                    fwd.srcMonitorIndex = a.isLocal ? a.monitorIndex : -1;
                    fwd.dstMonitorIndex = b.isLocal ? b.monitorIndex : -1;
                    fwd.srcStart = qRound((overlapTop - a.rect.top()) / a.rect.height() * 100);
                    fwd.srcEnd   = qRound((overlapBot - a.rect.top()) / a.rect.height() * 100);
                    fwd.dstStart = qRound((overlapTop - b.rect.top()) / b.rect.height() * 100);
                    fwd.dstEnd   = qRound((overlapBot - b.rect.top()) / b.rect.height() * 100);
                    addLink(fwd);

                    GeneratedLink rev;
                    rev.srcScreen = b.name;
                    rev.dstScreen = a.name;
                    rev.direction = "left";
                    rev.srcMonitorIndex = b.isLocal ? b.monitorIndex : -1;
                    rev.dstMonitorIndex = a.isLocal ? a.monitorIndex : -1;
                    rev.srcStart = fwd.dstStart;
                    rev.srcEnd   = fwd.dstEnd;
                    rev.dstStart = fwd.srcStart;
                    rev.dstEnd   = fwd.srcEnd;
                    addLink(rev);
                }
            }

            // bottom edge of a touching top edge of b → a:down=b, b:up=a
            if (std::abs(a.rect.bottom() - b.rect.top()) < ADJ_DIST) {
                qreal overlapLeft  = qMax(a.rect.left(), b.rect.left());
                qreal overlapRight = qMin(a.rect.right(), b.rect.right());
                if (overlapRight - overlapLeft > 10) {
                    GeneratedLink fwd;
                    fwd.srcScreen = a.name;
                    fwd.dstScreen = b.name;
                    fwd.direction = "down";
                    fwd.srcMonitorIndex = a.isLocal ? a.monitorIndex : -1;
                    fwd.dstMonitorIndex = b.isLocal ? b.monitorIndex : -1;
                    fwd.srcStart = qRound((overlapLeft - a.rect.left()) / a.rect.width() * 100);
                    fwd.srcEnd   = qRound((overlapRight - a.rect.left()) / a.rect.width() * 100);
                    fwd.dstStart = qRound((overlapLeft - b.rect.left()) / b.rect.width() * 100);
                    fwd.dstEnd   = qRound((overlapRight - b.rect.left()) / b.rect.width() * 100);
                    addLink(fwd);

                    GeneratedLink rev;
                    rev.srcScreen = b.name;
                    rev.dstScreen = a.name;
                    rev.direction = "up";
                    rev.srcMonitorIndex = b.isLocal ? b.monitorIndex : -1;
                    rev.dstMonitorIndex = a.isLocal ? a.monitorIndex : -1;
                    rev.srcStart = fwd.dstStart;
                    rev.srcEnd   = fwd.dstEnd;
                    rev.dstStart = fwd.srcStart;
                    rev.dstEnd   = fwd.srcEnd;
                    addLink(rev);
                }
            }
        }
    }

    return links;
}

void DisplayLayoutWidget::applyToServerConfig(ServerConfig* config) const
{
    QList<GeneratedLink> links = computeLinks();

    config->clearLinkConfigs();
    config->clearExplicitLinks();
    config->clearDisplayPositions();

    // save remote screen positions and sizes
    for (const DisplayRect& d : m_displays) {
        if (!d.isLocal) {
            config->setDisplayPosition(d.name, d.rect.topLeft());
            config->setDisplaySize(d.name, d.rect.size());
        }
    }

    // ensure all screens referenced in the layout exist in the screens list
    // (needed for the screens section output in the config file)
    for (const DisplayRect& d : m_displays)
        config->ensureScreen(d.name);

    for (const GeneratedLink& link : links) {
        LinkConfig lc;
        lc.srcStart = link.srcStart;
        lc.srcEnd = link.srcEnd;
        lc.dstStart = link.dstStart;
        lc.dstEnd = link.dstEnd;
        lc.monitorIndex = link.dstMonitorIndex;
        lc.srcMonitorIndex = link.srcMonitorIndex;

        config->setLinkConfig(link.srcScreen, link.direction, lc);

        ServerConfig::ExplicitLink el;
        el.srcScreen = link.srcScreen;
        el.dstScreen = link.dstScreen;
        el.direction = link.direction;
        el.config = lc;
        config->addExplicitLink(el);
    }
}

// --- painting ---

void DisplayLayoutWidget::paintEvent(QPaintEvent*)
{
    updateTransform();

    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);

    // background
    p.fillRect(rect(), QColor(42, 42, 46));

    // draw adjacency lines first (behind displays)
    QList<GeneratedLink> links = computeLinks();
    p.setPen(QPen(QColor(80, 200, 80, 180), 3));
    for (const GeneratedLink& link : links) {
        // find the two display rects
        for (int i = 0; i < m_displays.size(); ++i) {
            const DisplayRect& a = m_displays[i];
            if (a.name != link.srcScreen) continue;
            if (a.isLocal && a.monitorIndex != link.srcMonitorIndex && link.srcMonitorIndex >= 0) continue;
            if (!a.isLocal && link.srcMonitorIndex >= 0) continue;

            for (int j = 0; j < m_displays.size(); ++j) {
                const DisplayRect& b = m_displays[j];
                if (b.name != link.dstScreen) continue;
                if (b.isLocal && b.monitorIndex != link.dstMonitorIndex && link.dstMonitorIndex >= 0) continue;
                if (!b.isLocal && link.dstMonitorIndex >= 0) continue;

                QRectF wa = toWidget(a.rect);
                QRectF wb = toWidget(b.rect);

                if (link.direction == "right" || link.direction == "left") {
                    qreal x = (link.direction == "right") ? wa.right() : wa.left();
                    qreal yTop = qMax(wa.top(), wb.top());
                    qreal yBot = qMin(wa.bottom(), wb.bottom());
                    p.drawLine(QPointF(x, yTop), QPointF(x, yBot));
                } else {
                    qreal y = (link.direction == "down") ? wa.bottom() : wa.top();
                    qreal xL = qMax(wa.left(), wb.left());
                    qreal xR = qMin(wa.right(), wb.right());
                    p.drawLine(QPointF(xL, y), QPointF(xR, y));
                }
                break;
            }
            break;
        }
    }

    // draw displays
    for (int i = 0; i < m_displays.size(); ++i) {
        drawDisplay(p, m_displays[i], i == m_dragIndex);
    }

    // instructions
    p.setPen(QColor(160, 160, 160));
    p.setFont(QFont("sans-serif", 10));
    p.drawText(rect().adjusted(8, 0, -8, -8), Qt::AlignBottom | Qt::AlignHCenter,
               "Drag remote screens to position them next to your monitors. "
               "Green lines show active links.");
}

void DisplayLayoutWidget::drawDisplay(QPainter& p, const DisplayRect& d, bool selected) const
{
    QRectF wr = toWidget(d.rect);

    // fill
    QColor fill = d.isLocal ? QColor(55, 90, 140) : QColor(60, 120, 60);
    if (selected)
        fill = fill.lighter(140);
    p.setBrush(fill);

    // border
    QColor border = selected ? QColor(255, 200, 60) : QColor(180, 180, 180);
    p.setPen(QPen(border, selected ? 3.0 : 1.5));
    p.drawRoundedRect(wr, 6, 6);

    // label
    p.setPen(Qt::white);
    QFont f("sans-serif", qBound(9, (int)(wr.height() * 0.12), 16), QFont::Bold);
    p.setFont(f);
    p.drawText(wr, Qt::AlignCenter, d.displayLabel());

    // size subtitle
    QFont f2("sans-serif", qBound(7, (int)(wr.height() * 0.08), 11));
    p.setFont(f2);
    p.setPen(QColor(200, 200, 200, 180));
    QString sizeStr = QString("%1x%2").arg((int)d.rect.width()).arg((int)d.rect.height());
    p.drawText(wr.adjusted(0, wr.height() * 0.2, 0, 0), Qt::AlignCenter, sizeStr);

    // "local" / "remote" tag
    p.setFont(QFont("sans-serif", qBound(7, (int)(wr.height() * 0.07), 10)));
    p.setPen(QColor(180, 180, 180, 150));
    QString tag = d.isLocal ? "local" : "remote";
    p.drawText(wr.adjusted(4, 4, -4, -4), Qt::AlignTop | Qt::AlignLeft, tag);

    // resize grip for remote screens
    if (!d.isLocal) {
        qreal gs = 10;
        QPointF br = wr.bottomRight();
        p.setPen(QPen(QColor(200, 200, 200, 180), 1.5));
        p.drawLine(br + QPointF(-gs, -2), br + QPointF(-2, -gs));
        p.drawLine(br + QPointF(-gs + 4, -2), br + QPointF(-2, -gs + 4));
    }
}

// --- mouse handling ---

static bool isNearBottomRight(const QRectF& widgetRect, const QPointF& pos, qreal margin = 14.0)
{
    QPointF br = widgetRect.bottomRight();
    return (pos.x() >= br.x() - margin && pos.x() <= br.x() + 2 &&
            pos.y() >= br.y() - margin && pos.y() <= br.y() + 2);
}

void DisplayLayoutWidget::mousePressEvent(QMouseEvent* event)
{
    if (event->button() != Qt::LeftButton)
        return;

    int idx = hitTest(event->pos());
    if (idx >= 0 && !m_displays[idx].isLocal) {
        QRectF wr = toWidget(m_displays[idx].rect);
        if (isNearBottomRight(wr, event->pos())) {
            m_dragIndex = idx;
            m_resizing = true;
            setCursor(Qt::SizeFDiagCursor);
        } else {
            m_dragIndex = idx;
            m_resizing = false;
            QPointF logicalPos = toLogical(event->pos());
            m_dragOffset = logicalPos - m_displays[idx].rect.topLeft();
            setCursor(Qt::ClosedHandCursor);
        }
    }
}

void DisplayLayoutWidget::mouseMoveEvent(QMouseEvent* event)
{
    if (m_dragIndex >= 0) {
        QPointF logicalPos = toLogical(event->pos());
        if (m_resizing) {
            DisplayRect& d = m_displays[m_dragIndex];
            qreal newW = logicalPos.x() - d.rect.left();
            qreal newH = logicalPos.y() - d.rect.top();
            // enforce minimum size
            newW = qMax(newW, 400.0);
            newH = qMax(newH, 300.0);
            d.rect.setWidth(newW);
            d.rect.setHeight(newH);
        } else {
            QPointF newTopLeft = logicalPos - m_dragOffset;
            m_displays[m_dragIndex].rect.moveTopLeft(newTopLeft);
            snapToEdges(m_dragIndex);
        }
        update();
    } else {
        // hover cursor
        int idx = hitTest(event->pos());
        if (idx >= 0 && !m_displays[idx].isLocal) {
            QRectF wr = toWidget(m_displays[idx].rect);
            if (isNearBottomRight(wr, event->pos()))
                setCursor(Qt::SizeFDiagCursor);
            else
                setCursor(Qt::OpenHandCursor);
        } else {
            setCursor(Qt::ArrowCursor);
        }
    }
}

void DisplayLayoutWidget::mouseReleaseEvent(QMouseEvent* event)
{
    if (m_dragIndex >= 0) {
        m_dragIndex = -1;
        m_resizing = false;
        setCursor(Qt::ArrowCursor);
        emit layoutChanged();
        update();
    }
}
