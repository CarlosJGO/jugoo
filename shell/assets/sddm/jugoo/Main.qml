import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: root
    width: Screen.width
    height: Screen.height

    readonly property color bgColor: config.backgroundColor || "#04060E"
    readonly property color surfaceColor: config.surfaceColor || "#0B1020"
    readonly property color primaryColor: config.primaryColor || "#7C8CFF"
    readonly property color accentColor: config.accentColor || "#D946EF"
    readonly property color textColor: config.textColor || "#E8E9F2"
    readonly property color mutedColor: config.textMutedColor || "#9298B5"
    readonly property color borderColor: config.borderColor || "#29345C"
    readonly property color errorColor: config.errorColor || "#FF6B8A"
    readonly property color dimColor: config.dimColor || bgColor
    readonly property real dimAmount: parseFloat(config.dimBackground || "0.45")
    readonly property real formOpacity: parseFloat(config.formOpacity || "0.88")
    readonly property int formRadius: parseInt(config.formRadius || "12")
    readonly property bool showAvatar: (config.showAvatar || "true") === "true"
    readonly property bool showClock: (config.showClock || "true") === "true"
    readonly property bool cropBackground: (config.backgroundFill || "crop") === "crop"

    property int sessionIndex: sessionModel.lastIndex
    property string errorText: ""

    function doLogin() {
        errorText = ""
        sddm.login(usernameField.text, passwordField.text, sessionIndex)
    }

    Rectangle {
        anchors.fill: parent
        color: root.bgColor
        z: -2
    }

    Image {
        id: wallpaper
        anchors.fill: parent
        z: -1
        visible: (config.background || "") !== ""
        source: (config.background || "") !== "" ? Qt.resolvedUrl(config.background) : ""
        fillMode: root.cropBackground ? Image.PreserveAspectCrop : Image.PreserveAspectFit
        asynchronous: true
        cache: true
        onStatusChanged: {
            if (status === Image.Error)
                visible = false
        }
    }

    Rectangle {
        anchors.fill: parent
        color: root.dimColor
        opacity: root.dimAmount
        z: 0
    }

    Column {
        id: clockBlock
        visible: root.showClock
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: parent.height * 0.08
        spacing: 6
        z: 1

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: Qt.formatTime(clockTimer.date, "HH:mm")
            color: root.textColor
            font.pixelSize: 56
            font.weight: Font.Light
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: Qt.formatDate(clockTimer.date, Locale.LongFormat)
            color: root.mutedColor
            font.pixelSize: 16
        }
    }

    Timer {
        id: clockTimer
        property date date: new Date()
        interval: 1000
        running: root.showClock
        repeat: true
        onTriggered: date = new Date()
    }

    Item {
        id: form
        width: Math.min(380, parent.width * 0.86)
        height: formColumn.implicitHeight + 48
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        anchors.verticalCenterOffset: root.showClock ? parent.height * 0.06 : 0
        z: 2

        Rectangle {
            anchors.fill: parent
            radius: root.formRadius
            color: root.surfaceColor
            opacity: root.formOpacity
            border.color: root.borderColor
            border.width: 1
        }

        ColumnLayout {
            id: formColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 24
            spacing: 14

            Item {
                visible: root.showAvatar
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 72
                Layout.preferredHeight: 72

                Rectangle {
                    anchors.fill: parent
                    radius: width / 2
                    color: root.borderColor
                    Text {
                        anchors.centerIn: parent
                        text: (usernameField.text || "?").charAt(0).toUpperCase()
                        color: root.textColor
                        font.pixelSize: 28
                    }
                }
            }

            Text {
                Layout.alignment: Qt.AlignHCenter
                text: "Jugoo"
                color: root.primaryColor
                font.pixelSize: 18
                font.weight: Font.Medium
            }

            TextField {
                id: usernameField
                Layout.fillWidth: true
                text: userModel.lastUser
                placeholderText: "Usuario"
                color: root.textColor
                placeholderTextColor: root.mutedColor
                selectByMouse: true
                background: Rectangle {
                    radius: Math.max(6, root.formRadius - 4)
                    color: root.bgColor
                    border.color: usernameField.activeFocus ? root.primaryColor : root.borderColor
                    border.width: 1
                    opacity: 0.95
                }
                Keys.onPressed: function (event) {
                    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                        passwordField.forceActiveFocus()
                        event.accepted = true
                    }
                }
            }

            TextField {
                id: passwordField
                Layout.fillWidth: true
                placeholderText: "Contraseña"
                echoMode: TextInput.Password
                color: root.textColor
                placeholderTextColor: root.mutedColor
                selectByMouse: true
                background: Rectangle {
                    radius: Math.max(6, root.formRadius - 4)
                    color: root.bgColor
                    border.color: passwordField.activeFocus ? root.accentColor : root.borderColor
                    border.width: 1
                    opacity: 0.95
                }
                Keys.onPressed: function (event) {
                    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                        root.doLogin()
                        event.accepted = true
                    }
                }
            }

            ComboBox {
                id: sessionCombo
                Layout.fillWidth: true
                model: sessionModel
                textRole: "name"
                currentIndex: sessionModel.lastIndex
                onCurrentIndexChanged: root.sessionIndex = currentIndex
                contentItem: Text {
                    leftPadding: 12
                    text: sessionCombo.displayText
                    color: root.textColor
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
                background: Rectangle {
                    radius: Math.max(6, root.formRadius - 4)
                    color: root.bgColor
                    border.color: root.borderColor
                    border.width: 1
                }
            }

            Text {
                Layout.fillWidth: true
                visible: root.errorText.length > 0
                text: root.errorText
                color: root.errorColor
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                font.pixelSize: 13
            }

            Button {
                id: loginButton
                Layout.fillWidth: true
                text: "Entrar"
                onClicked: root.doLogin()
                contentItem: Text {
                    text: loginButton.text
                    color: root.textColor
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    font.pixelSize: 15
                    font.weight: Font.Medium
                }
                background: Rectangle {
                    radius: Math.max(6, root.formRadius - 4)
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: root.primaryColor }
                        GradientStop { position: 1.0; color: root.accentColor }
                    }
                    opacity: loginButton.down ? 0.75 : 1.0
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Button {
                    Layout.fillWidth: true
                    text: "Suspender"
                    onClicked: sddm.suspend()
                    contentItem: Text {
                        text: parent.text
                        color: root.mutedColor
                        horizontalAlignment: Text.AlignHCenter
                        font.pixelSize: 12
                    }
                    background: Rectangle {
                        radius: Math.max(4, root.formRadius - 6)
                        color: "transparent"
                        border.color: root.borderColor
                        border.width: 1
                    }
                }
                Button {
                    Layout.fillWidth: true
                    text: "Reiniciar"
                    onClicked: sddm.reboot()
                    contentItem: Text {
                        text: parent.text
                        color: root.mutedColor
                        horizontalAlignment: Text.AlignHCenter
                        font.pixelSize: 12
                    }
                    background: Rectangle {
                        radius: Math.max(4, root.formRadius - 6)
                        color: "transparent"
                        border.color: root.borderColor
                        border.width: 1
                    }
                }
                Button {
                    Layout.fillWidth: true
                    text: "Apagar"
                    onClicked: sddm.powerOff()
                    contentItem: Text {
                        text: parent.text
                        color: root.mutedColor
                        horizontalAlignment: Text.AlignHCenter
                        font.pixelSize: 12
                    }
                    background: Rectangle {
                        radius: Math.max(4, root.formRadius - 6)
                        color: "transparent"
                        border.color: root.borderColor
                        border.width: 1
                    }
                }
            }
        }
    }

    Connections {
        target: sddm
        function onLoginFailed() {
            root.errorText = "Acceso denegado"
            passwordField.text = ""
            passwordField.forceActiveFocus()
        }
        function onLoginSucceeded() {
            root.errorText = ""
        }
    }

    Component.onCompleted: {
        if (usernameField.text.length > 0)
            passwordField.forceActiveFocus()
        else
            usernameField.forceActiveFocus()
    }
}
