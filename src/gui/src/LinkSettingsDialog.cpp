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

#include "LinkSettingsDialog.h"

#include <QDialogButtonBox>
#include <QFormLayout>
#include <QGroupBox>
#include <QLabel>
#include <QSpinBox>
#include <QVBoxLayout>

LinkSettingsDialog::LinkSettingsDialog(QWidget* parent, const QString& srcScreen,
                                       const QString& dstScreen,
                                       const QString& direction,
                                       const LinkConfig& config) :
    QDialog(parent, Qt::WindowTitleHint | Qt::WindowSystemMenuHint)
{
    setWindowTitle(tr("Link Settings: %1 %2 %3")
                   .arg(srcScreen, direction, dstScreen));

    QVBoxLayout* mainLayout = new QVBoxLayout(this);

    // Source edge group
    QGroupBox* srcGroup = new QGroupBox(
        tr("Source edge (%1, %2 side)").arg(srcScreen, direction));
    QFormLayout* srcLayout = new QFormLayout(srcGroup);

    m_pSrcStart = new QSpinBox();
    m_pSrcStart->setRange(0, 99);
    m_pSrcStart->setSuffix("%");
    m_pSrcStart->setValue(config.srcStart);
    srcLayout->addRow(tr("Start:"), m_pSrcStart);

    m_pSrcEnd = new QSpinBox();
    m_pSrcEnd->setRange(1, 100);
    m_pSrcEnd->setSuffix("%");
    m_pSrcEnd->setValue(config.srcEnd);
    srcLayout->addRow(tr("End:"), m_pSrcEnd);

    mainLayout->addWidget(srcGroup);

    // Destination edge group
    QGroupBox* dstGroup = new QGroupBox(
        tr("Destination edge (%1)").arg(dstScreen));
    QFormLayout* dstLayout = new QFormLayout(dstGroup);

    m_pDstStart = new QSpinBox();
    m_pDstStart->setRange(0, 99);
    m_pDstStart->setSuffix("%");
    m_pDstStart->setValue(config.dstStart);
    dstLayout->addRow(tr("Start:"), m_pDstStart);

    m_pDstEnd = new QSpinBox();
    m_pDstEnd->setRange(1, 100);
    m_pDstEnd->setSuffix("%");
    m_pDstEnd->setValue(config.dstEnd);
    dstLayout->addRow(tr("End:"), m_pDstEnd);

    m_pMonitorIndex = new QSpinBox();
    m_pMonitorIndex->setRange(-1, 15);
    m_pMonitorIndex->setSpecialValueText(tr("All (default)"));
    m_pMonitorIndex->setValue(config.monitorIndex);
    dstLayout->addRow(tr("Target monitor:"), m_pMonitorIndex);

    mainLayout->addWidget(dstGroup);

    // Source monitor group
    QGroupBox* srcMonGroup = new QGroupBox(
        tr("Source monitor (%1)").arg(srcScreen));
    QFormLayout* srcMonLayout = new QFormLayout(srcMonGroup);

    m_pSrcMonitorIndex = new QSpinBox();
    m_pSrcMonitorIndex->setRange(-1, 15);
    m_pSrcMonitorIndex->setSpecialValueText(tr("Whole screen (default)"));
    m_pSrcMonitorIndex->setValue(config.srcMonitorIndex);
    srcMonLayout->addRow(tr("Source monitor:"), m_pSrcMonitorIndex);

    mainLayout->addWidget(srcMonGroup);

    // Help text
    QLabel* helpLabel = new QLabel(tr(
        "Intervals are percentages (0-100) of the screen edge, "
        "measured from top-left.\n"
        "Example: 0-60 maps the top 60%% of the edge.\n"
        "Target monitor -1 means the whole screen (default)."));
    helpLabel->setWordWrap(true);
    mainLayout->addWidget(helpLabel);

    // Buttons
    QDialogButtonBox* buttons = new QDialogButtonBox(
        QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
    connect(buttons, &QDialogButtonBox::accepted, this, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
    mainLayout->addWidget(buttons);

    setLayout(mainLayout);
    resize(350, 450);
}

LinkConfig LinkSettingsDialog::linkConfig() const
{
    LinkConfig config;
    config.srcStart = m_pSrcStart->value();
    config.srcEnd = m_pSrcEnd->value();
    config.dstStart = m_pDstStart->value();
    config.dstEnd = m_pDstEnd->value();
    config.monitorIndex = m_pMonitorIndex->value();
    config.srcMonitorIndex = m_pSrcMonitorIndex->value();
    return config;
}
