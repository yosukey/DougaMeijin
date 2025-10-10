# list_delegate.py
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QFont, QPainter, QBrush, QColor, QFontMetrics
from PySide6.QtWidgets import QApplication, QStyledItemDelegate, QStyle, QStyleOptionViewItem

class RichTextDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._overlay_font_cache = {}

    def get_overlay_font(self, base_font: QFont) -> QFont:
        base_size = base_font.pointSizeF()
        if base_size <= 0 and base_font.pixelSize() > 0:
            base_size = base_font.pixelSize() * 0.75
        elif base_size <= 0:
             base_size = 16

        if base_size in self._overlay_font_cache:
            return self._overlay_font_cache[base_size]

        target_pt_size = max(base_size * 1.5, 18.0)
        
        overlay_font = QFont(["Segoe UI Emoji", "Meiryo"])
        overlay_font.setPointSizeF(target_pt_size)
        overlay_font.setBold(True)
        
        self._overlay_font_cache[base_size] = overlay_font
        
        return overlay_font

    def paint(self, painter, option, index):
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)

        style = options.widget.style() if options.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, options, painter)

        is_locked = index.data(Qt.UserRole + 1)
        has_audio = index.data(Qt.UserRole + 2)

        if not is_locked and not has_audio:
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        overlay_font = self.get_overlay_font(options.font)
        painter.setFont(overlay_font)
        
        icon_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, options, options.widget)

        draw_flags_center = Qt.AlignCenter | Qt.TextDontClip
        
        fm = QFontMetrics(overlay_font)
        icon_height = fm.height()
        icon_size = int(icon_height * 1.2)
        
        padding = 4
        bg_diameter = icon_size

        bg_brush = QBrush(QColor(0, 0, 0, 140))
        painter.setPen(Qt.NoPen)

        if is_locked:
            bg_rect_topright = QRect(
                icon_rect.right() - bg_diameter - padding,
                icon_rect.top() + padding,
                bg_diameter,
                bg_diameter
            )
            painter.setBrush(bg_brush)
            painter.drawEllipse(bg_rect_topright)
            
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(bg_rect_topright, draw_flags_center, "🔒")

        if has_audio:
            painter.setPen(Qt.NoPen)
            bg_rect_topleft = QRect(
                icon_rect.left() + padding,
                icon_rect.top() + padding,
                bg_diameter,
                bg_diameter
            )
            painter.setBrush(bg_brush)
            painter.drawEllipse(bg_rect_topleft)
            
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(bg_rect_topleft, draw_flags_center, "🔊")

        painter.restore()

    def sizeHint(self, option, index):
        return super().sizeHint(option, index)