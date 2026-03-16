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

#if !defined(DISPLAYLAYOUTWIDGET__H)
#define DISPLAYLAYOUTWIDGET__H

#include <QWidget>
#include <QVector>
#include <QRectF>
#include <QString>
#include <QMap>
#include <QPointF>

class ServerConfig;

struct DisplayRect {
    QString name;        // screen name (e.g. "MBP" or "iMac")
    int monitorIndex;    // -1 for remote screens, 0+ for local monitors
    QRectF rect;         // position/size in logical layout coords
    bool isLocal;        // true = local monitor, false = remote screen

    QString displayLabel() const {
        if (isLocal && monitorIndex >= 0)
            return QString("%1@%2").arg(name).arg(monitorIndex);
        return name;
    }
};

struct GeneratedLink {
    QString srcScreen;
    QString dstScreen;
    QString direction;       // "left", "right", "up", "down"
    int srcMonitorIndex;     // -1 = whole screen
    int dstMonitorIndex;     // -1 = whole screen
    int srcStart, srcEnd;    // 0-100
    int dstStart, dstEnd;    // 0-100
};

class DisplayLayoutWidget : public QWidget
{
    Q_OBJECT

public:
    DisplayLayoutWidget(QWidget* parent = nullptr);

    void setServerName(const QString& name);
    void setServerConfig(ServerConfig* config) { m_pServerConfig = config; }
    void setRemoteScreens(const QStringList& screenNames);

    QList<GeneratedLink> computeLinks() const;
    void applyToServerConfig(ServerConfig* config) const;

signals:
    void layoutChanged();

protected:
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;

private:
    void rebuildLocalMonitors();
    void layoutRemoteScreens();

    // coordinate transforms
    QRectF toWidget(const QRectF& logical) const;
    QPointF toLogical(const QPointF& widget) const;
    void updateTransform();

    // hit testing
    int hitTest(const QPointF& widgetPos) const;

    // snapping
    void snapToEdges(int dragIndex);

    // drawing
    void drawDisplay(QPainter& p, const DisplayRect& d, bool selected) const;

    QString m_serverName;
    ServerConfig* m_pServerConfig;
    QVector<DisplayRect> m_displays;

    // drag/resize state
    int m_dragIndex;
    QPointF m_dragOffset;   // offset in logical coords
    bool m_resizing;        // true if resizing instead of moving
    int m_resizeCorner;     // 0=none, 1=bottomRight

    // view transform
    qreal m_scale;
    QPointF m_viewOffset;

    static constexpr qreal SNAP_DIST = 50.0;
    static constexpr qreal ADJ_DIST = 15.0;
    static constexpr qreal DEFAULT_REMOTE_W = 1920.0;
    static constexpr qreal DEFAULT_REMOTE_H = 1080.0;
};

#endif
