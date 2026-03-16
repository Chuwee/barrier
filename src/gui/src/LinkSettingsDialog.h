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

#if !defined(LINKSETTINGSDIALOG__H)

#define LINKSETTINGSDIALOG__H

#include <QDialog>

#include "LinkConfig.h"

class QSpinBox;
class QWidget;

class LinkSettingsDialog : public QDialog
{
    Q_OBJECT

    public:
        LinkSettingsDialog(QWidget* parent, const QString& srcScreen,
                           const QString& dstScreen, const QString& direction,
                           const LinkConfig& config);

        LinkConfig linkConfig() const;

    private:
        QSpinBox* m_pSrcStart;
        QSpinBox* m_pSrcEnd;
        QSpinBox* m_pDstStart;
        QSpinBox* m_pDstEnd;
        QSpinBox* m_pMonitorIndex;
        QSpinBox* m_pSrcMonitorIndex;
};

#endif
