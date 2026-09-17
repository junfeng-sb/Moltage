"""Native Project Manager sort control and three presentation delegates."""

from collections.abc import Callable

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QToolTip,
    QWidget,
)

from moltage.app.density_workflow import DensityTask
from moltage.app.project_presentation import (
    ProjectPresentationRecord,
    ProjectSortCriterion,
    ProjectStepIndicator,
    ProjectViewMode,
    StepIndicatorKind,
    format_project_submission_timestamp,
)


PROJECT_PRESENTATION_ROLE = int(Qt.ItemDataRole.UserRole) + 1
PROJECT_UUID_ROLE = int(Qt.ItemDataRole.UserRole) + 2
TILE_GRID_SIZE = QSize(136, 104)
TILE_ITEM_SIZE = QSize(126, 94)
_DENSITY_COMPONENT_NAMES = ("Total", "Subset 1", "Subset 2")


def density_component_indicators(
    task: DensityTask,
) -> tuple[ProjectStepIndicator, ...]:
    """Adapt the three density component states to the ordinary lamp style."""

    kinds = {
        "COMPLETE": StepIndicatorKind.SUCCEEDED,
        "FAILED": StepIndicatorKind.FAILED,
        "CANCELLED": StepIndicatorKind.CANCELLED,
        "NOT_STARTED": StepIndicatorKind.NOT_STARTED,
        "UNKNOWN": StepIndicatorKind.UNKNOWN,
        "UNRESOLVED": StepIndicatorKind.UNKNOWN,
    }
    states = task.component_states
    return tuple(
        ProjectStepIndicator(
            index,
            kinds.get(states[component], StepIndicatorKind.ACTIVE),
            f"{label} — {states[component]}",
        )
        for index, (component, label) in enumerate(
            zip(states, _DENSITY_COMPONENT_NAMES), 1
        )
    )


class ProjectSortButton(QToolButton):
    """Menu-backed visible sort criterion; double-click reverses its total order."""

    criterion_changed = Signal(object)
    reverse_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectsSort")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        menu = QMenu(self)
        for criterion in ProjectSortCriterion:
            action = menu.addAction(criterion.value)
            action.setData(criterion)
            action.triggered.connect(
                lambda _checked=False, selected=criterion: (
                    self.criterion_changed.emit(selected)
                )
            )
        self.setMenu(menu)
        self.setToolTip(
            "Choose project ordering. Double-click this control to reverse it."
        )

    def update_label(
        self,
        criterion: ProjectSortCriterion,
        *,
        reversed_order: bool,
    ) -> None:
        normal_descending = criterion is ProjectSortCriterion.SUBMISSION_DATE
        descending = normal_descending != reversed_order
        self.setText(f"Sort: {criterion.value} {'↓' if descending else '↑'}")

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.reverse_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ProjectItemDelegate(QStyledItemDelegate):
    """Paint one canonical record as Details, Compact, or a fixed tile."""

    def __init__(
        self,
        mode_provider: Callable[[], ProjectViewMode],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode_provider = mode_provider

    def sizeHint(self, option, index) -> QSize:
        mode = self._mode_provider()
        if mode is ProjectViewMode.TILES:
            return TILE_ITEM_SIZE
        if mode is ProjectViewMode.COMPACT:
            return QSize(max(option.rect.width(), 300), 34)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        lines = max(1, text.count("\n") + 1)
        height = max(48, lines * QFontMetrics(option.font).lineSpacing() + 14)
        return QSize(max(option.rect.width(), 300), height)

    def paint(self, painter: QPainter, option, index) -> None:
        record = index.data(PROJECT_PRESENTATION_ROLE)
        if not isinstance(record, (ProjectPresentationRecord, DensityTask)):
            super().paint(painter, option, index)
            return
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        background = (
            option.palette.highlight().color()
            if selected
            else option.palette.base().color()
        )
        foreground = (
            option.palette.highlightedText().color()
            if selected
            else option.palette.text().color()
        )
        painter.fillRect(option.rect, background)
        painter.setPen(foreground)
        mode = self._mode_provider()
        if isinstance(record, DensityTask):
            self._paint_density(painter, option, index, record, foreground)
        elif mode is ProjectViewMode.DETAILS:
            self._paint_details(painter, option, index, record, foreground)
        elif mode is ProjectViewMode.COMPACT:
            self._paint_compact(painter, option, record, foreground)
        else:
            self._paint_tile(painter, option, record, foreground)
        painter.restore()

    def helpEvent(self, event, view, option, index) -> bool:
        if event.type() != QEvent.Type.ToolTip:
            return super().helpEvent(event, view, option, index)
        record = index.data(PROJECT_PRESENTATION_ROLE)
        if not isinstance(record, (ProjectPresentationRecord, DensityTask)):
            return super().helpEvent(event, view, option, index)
        if isinstance(record, DensityTask):
            for indicator, rectangle in zip(
                density_component_indicators(record),
                self.density_indicator_rects(
                    option.rect, self._mode_provider()
                ),
            ):
                if rectangle.contains(event.pos()):
                    QToolTip.showText(event.globalPos(), indicator.tooltip, view)
                    return True
            if self._mode_provider() is ProjectViewMode.TILES:
                QToolTip.showText(event.globalPos(), record.name, view)
                return True
            return super().helpEvent(event, view, option, index)
        for indicator, rectangle in zip(
            record.indicators,
            self.indicator_rects(
                option.rect,
                self._mode_provider(),
                len(record.indicators),
            ),
        ):
            if rectangle.contains(event.pos()):
                QToolTip.showText(event.globalPos(), indicator.tooltip, view)
                return True
        if self._mode_provider() is ProjectViewMode.TILES:
            QToolTip.showText(event.globalPos(), record.display_name, view)
            return True
        return super().helpEvent(event, view, option, index)

    @staticmethod
    def indicator_rects(
        rect: QRect,
        mode: ProjectViewMode,
        count: int = 4,
    ) -> tuple[QRect, ...]:
        diameter = 17 if mode is ProjectViewMode.TILES else 18
        spacing = 5
        total = count * diameter + max(0, count - 1) * spacing
        if mode is ProjectViewMode.TILES:
            left = rect.left() + max(6, (rect.width() - total) // 2)
            top = rect.bottom() - diameter - 8
        else:
            left = rect.right() - total - 10
            top = rect.center().y() - diameter // 2
        return tuple(
            QRect(left + index * (diameter + spacing), top, diameter, diameter)
            for index in range(count)
        )

    @classmethod
    def density_indicator_rects(
        cls, rect: QRect, mode: ProjectViewMode
    ) -> tuple[QRect, ...]:
        return cls.indicator_rects(rect, mode, 3)

    def _paint_density(
        self,
        painter: QPainter,
        option,
        index,
        task: DensityTask,
        foreground: QColor,
    ) -> None:
        mode = self._mode_provider()
        indicators = density_component_indicators(task)
        rectangles = self.density_indicator_rects(option.rect, mode)
        if mode is ProjectViewMode.TILES:
            content = option.rect.adjusted(7, 6, -7, -6)
            line_height = QFontMetrics(option.font).lineSpacing()
            title_rect = QRect(
                content.left(), content.top(), content.width(), line_height + 2
            )
            detail_rect = QRect(
                content.left(),
                title_rect.bottom() + 6,
                content.width(),
                line_height + 2,
            )
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(foreground)
            painter.drawText(
                title_rect,
                Qt.AlignmentFlag.AlignCenter,
                QFontMetrics(option.font).elidedText(
                    task.name,
                    Qt.TextElideMode.ElideRight,
                    title_rect.width(),
                ),
            )
            font.setBold(False)
            painter.setFont(font)
            painter.drawText(
                detail_rect,
                Qt.AlignmentFlag.AlignCenter,
                "Density Difference",
            )
        else:
            right = rectangles[0].left() - 8
            text_rect = QRect(
                option.rect.left() + 8,
                option.rect.top() + (5 if mode is ProjectViewMode.DETAILS else 0),
                max(0, right - option.rect.left() - 8),
                option.rect.height() - (10 if mode is ProjectViewMode.DETAILS else 0),
            )
            text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            if mode is ProjectViewMode.COMPACT:
                text = text.replace("\n", " — ")
                text = QFontMetrics(option.font).elidedText(
                    text, Qt.TextElideMode.ElideRight, text_rect.width()
                )
            painter.setPen(foreground)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
        self._paint_indicators(painter, indicators, rectangles)

    def _paint_details(
        self,
        painter: QPainter,
        option,
        index,
        record: ProjectPresentationRecord,
        foreground: QColor,
    ) -> None:
        indicators = self.indicator_rects(
            option.rect,
            ProjectViewMode.DETAILS,
            len(record.indicators),
        )
        text_rect = option.rect.adjusted(8, 5, -(option.rect.width() // 5), -5)
        painter.setPen(foreground)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
        )
        self._paint_indicators(painter, record.indicators, indicators)

    def _paint_compact(
        self,
        painter: QPainter,
        option,
        record: ProjectPresentationRecord,
        foreground: QColor,
    ) -> None:
        indicators = self.indicator_rects(
            option.rect,
            ProjectViewMode.COMPACT,
            len(record.indicators),
        )
        right = indicators[0].left() - 8
        text_rect = QRect(
            option.rect.left() + 8,
            option.rect.top(),
            max(0, right - option.rect.left() - 8),
            option.rect.height(),
        )
        painter.setPen(foreground)
        title = QFontMetrics(option.font).elidedText(
            record.remote_directory_basename,
            Qt.TextElideMode.ElideRight,
            text_rect.width(),
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, title)
        self._paint_indicators(painter, record.indicators, indicators)

    def _paint_tile(
        self,
        painter: QPainter,
        option,
        record: ProjectPresentationRecord,
        foreground: QColor,
    ) -> None:
        content = option.rect.adjusted(7, 6, -7, -6)
        line_height = QFontMetrics(option.font).lineSpacing()
        title_rect = QRect(content.left(), content.top(), content.width(), line_height + 2)
        date_rect = QRect(
            content.left(),
            title_rect.bottom() + 6,
            content.width(),
            line_height + 2,
        )
        title = QFontMetrics(option.font).elidedText(
            record.display_name,
            Qt.TextElideMode.ElideRight,
            title_rect.width(),
        )
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(foreground)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, title)
        font.setBold(False)
        painter.setFont(font)
        painter.drawText(
            date_rect,
            Qt.AlignmentFlag.AlignCenter,
            format_project_submission_timestamp(record.submitted_at),
        )
        self._paint_indicators(
            painter,
            record.indicators,
            self.indicator_rects(
                option.rect,
                ProjectViewMode.TILES,
                len(record.indicators),
            ),
        )

    def _paint_indicators(
        self,
        painter: QPainter,
        indicators: tuple[ProjectStepIndicator, ...],
        rectangles: tuple[QRect, ...],
    ) -> None:
        for indicator, rectangle in zip(indicators, rectangles):
            color = _INDICATOR_COLORS[indicator.kind]
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(color, 1.5))
            if indicator.kind in _UNFILLED_INDICATOR_KINDS:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            else:
                painter.setBrush(color)
            painter.drawEllipse(rectangle)
            text = (
                "?"
                if indicator.kind is StepIndicatorKind.UNKNOWN
                else str(indicator.step_number)
            )
            painter.setPen(
                QColor("#202020")
                if indicator.kind
                in {
                    StepIndicatorKind.ACTIVE,
                    StepIndicatorKind.NOT_STARTED,
                    StepIndicatorKind.UNKNOWN,
                }
                else QColor("#ffffff")
            )
            painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter, text)


_INDICATOR_COLORS = {
    StepIndicatorKind.SUCCEEDED: QColor("#2e7d32"),
    StepIndicatorKind.SKIPPED: QColor("#757575"),
    StepIndicatorKind.ACTIVE: QColor("#fbc02d"),
    StepIndicatorKind.FAILED: QColor("#c62828"),
    StepIndicatorKind.CANCELLED: QColor("#616161"),
    StepIndicatorKind.NOT_STARTED: QColor("#757575"),
    StepIndicatorKind.UNKNOWN: QColor("#fbc02d"),
}

_UNFILLED_INDICATOR_KINDS = frozenset({StepIndicatorKind.NOT_STARTED})
