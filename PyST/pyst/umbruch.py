"""
Umbrechende Bedienzeile (UmbruchLayout): verhaelt sich wie ein QHBoxLayout, solange alles in eine Zeile passt
(Stretch, Abstaende, Wunschbreiten bleiben), und bricht bei zu wenig Breite in weitere Zeilen um.

Elemente sind Widgets, Unter-Layouts oder Gruppen (gruppe=…): eine Gruppe bleibt beim Umbruch zusammen (z.B.
„Fahrzeug“ + Auswahl). Umgebrochen wird erst, wenn die Elemente sonst unter ihre Mindestbreite kaemen; die
Mindestbreite der ganzen Zeile ist die der breitesten Gruppe. Eine Gruppe ohne Mindestbreite (Hinweis, der sich
kuerzt) bricht nie um, sie bekommt den Rest der Zeile. So bleiben Texte und Knoepfe vollstaendig lesbar, auch
bei schmalen Fenstern (MacBook 1280 Punkte).
"""
import itertools
from typing import List, Optional

from PySide6 import QtCore, QtWidgets

_ids = itertools.count(1)


class _Eintrag:
    __slots__ = ("item", "stretch", "breite", "abstand", "gruppe")

    def __init__(self, item, stretch: int = 0, breite: int = 0, abstand: bool = False, gruppe: int = 0):
        self.item = item
        self.stretch = stretch
        self.breite = breite              # Wunschbreite (statt sizeHint, wenn Platz ist), 0 = sizeHint
        self.abstand = abstand            # fester Abstand (addSpacing) – faellt am Zeilenanfang/-ende weg
        self.gruppe = gruppe or next(_ids)

    def stretch_platz(self) -> bool:
        return isinstance(self.item, QtWidgets.QSpacerItem) and not self.abstand


class UmbruchLayout(QtWidgets.QLayout):
    """Zeile mit Umbruch. add(widget_oder_layout, stretch, breite, gruppe), add_gruppe(...), addStretch(n),
    addSpacing(px)."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, abstand: int = 6, zeilenabstand: int = 4):
        super().__init__(parent)
        self._eintraege: List[_Eintrag] = []
        self._h = abstand
        self._v = zeilenabstand
        self._breite_zuletzt = -1
        self._hoehe_zuletzt = -1
        if parent is None:
            self.setContentsMargins(0, 0, 0, 0)

    # ------------------------------------------------------------ Befuellen
    @staticmethod
    def neue_gruppe() -> int:
        return next(_ids)

    def addItem(self, item: QtWidgets.QLayoutItem):          # von addWidget aufgerufen
        self._eintraege.append(_Eintrag(item))

    def add(self, x, stretch: int = 0, breite: int = 0, gruppe: int = 0):
        """Widget oder Layout anhaengen; breite = Wunschbreite bei genug Platz; gleiche gruppe = bleibt zusammen."""
        if isinstance(x, QtWidgets.QLayout):
            self.addChildLayout(x)
            self._eintraege.append(_Eintrag(x, stretch, breite, False, gruppe))
        else:
            self.addWidget(x)
            e = self._eintraege[-1]
            e.stretch, e.breite = stretch, breite
            if gruppe:
                e.gruppe = gruppe
        self.invalidate()
        return x

    def add_gruppe(self, *elemente, stretch: Optional[dict] = None, breite: Optional[dict] = None) -> int:
        """Mehrere Widgets als Gruppe; stretch/breite: {widget: wert}."""
        g = self.neue_gruppe()
        for x in elemente:
            self.add(x, (stretch or {}).get(x, 0), (breite or {}).get(x, 0), g)
        return g

    def addStretch(self, stretch: int = 1):
        sp = QtWidgets.QSpacerItem(0, 0, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        self._eintraege.append(_Eintrag(sp, stretch))
        self.invalidate()

    def addSpacing(self, px: int, gruppe: int = 0):
        sp = QtWidgets.QSpacerItem(px, 0, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
        self._eintraege.append(_Eintrag(sp, 0, 0, True, gruppe))
        self.invalidate()

    def leeren(self):
        """Alle Eintraege herausnehmen (die Widgets bleiben bestehen und koennen neu hinzugefuegt werden)."""
        self._eintraege.clear()
        self.invalidate()

    def count(self) -> int:
        return len(self._eintraege)

    def itemAt(self, i: int):
        return self._eintraege[i].item if 0 <= i < len(self._eintraege) else None

    def takeAt(self, i: int):
        if 0 <= i < len(self._eintraege):
            return self._eintraege.pop(i).item
        return None

    def setSpacing(self, px: int):
        self._h = px
        self.invalidate()

    def spacing(self) -> int:
        return self._h

    # ------------------------------------------------------------ Groessen
    def expandingDirections(self):
        richtung = QtCore.Qt.Horizontal
        for e in self._eintraege:
            if not isinstance(e.item, QtWidgets.QSpacerItem) and e.item.expandingDirections() & QtCore.Qt.Vertical:
                return richtung | QtCore.Qt.Vertical
        return richtung

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, breite: int) -> int:
        return self._anordnen(QtCore.QRect(0, 0, breite, 0), setzen=False)

    def _sichtbar(self) -> List[_Eintrag]:
        return [e for e in self._eintraege if not e.item.isEmpty() or isinstance(e.item, QtWidgets.QSpacerItem)]

    def _gruppen(self) -> List[List[_Eintrag]]:
        out: List[List[_Eintrag]] = []
        for e in self._sichtbar():
            if out and out[-1][0].gruppe == e.gruppe:
                out[-1].append(e)
            else:
                out.append([e])
        return out

    def _rand(self):
        m = self.contentsMargins()
        return m.left() + m.right(), m.top() + m.bottom()

    @staticmethod
    def _min(e: _Eintrag) -> int:
        return e.item.minimumSize().width()

    @staticmethod
    def _wunsch(e: _Eintrag) -> int:
        w = e.item.widget()
        hint = e.item.sizeHint().width()
        if w is not None and w.sizePolicy().horizontalPolicy() == QtWidgets.QSizePolicy.Ignored:
            hint = 0                                      # wie im QBoxLayout: Ignored hat keinen Platzwunsch
        return max(hint, e.breite, e.item.minimumSize().width())

    def _abstaende(self, z: List[_Eintrag]) -> List[int]:
        """Abstand vor jedem Element wie im QBoxLayout: nur zwischen zwei Nicht-Abstand-Elementen, Abstaende und
        Stretch zaehlen nur mit ihrer eigenen Breite."""
        out, vorher = [], False
        for e in z:
            if isinstance(e.item, QtWidgets.QSpacerItem):
                out.append(0)
            else:
                out.append(self._h if vorher else 0)
                vorher = True
        return out

    def _breite_zeile(self, z: List[_Eintrag], f) -> int:
        return sum(f(e) for e in z) + sum(self._abstaende(z))

    def sizeHint(self) -> QtCore.QSize:
        rb, rh = self._rand()
        es = self._sichtbar()
        b = self._breite_zeile(es, self._wunsch)
        h = max([e.item.sizeHint().height() for e in es if not isinstance(e.item, QtWidgets.QSpacerItem)] or [0])
        return QtCore.QSize(b + rb, h + rh)

    def minimumSize(self) -> QtCore.QSize:
        rb, _rh = self._rand()
        b = max([self._breite_zeile(g, self._min) for g in self._gruppen()] or [0])
        # Hoehe: so viele Zeilen, wie bei der zuletzt gesetzten Breite noetig (sonst eine Zeile)
        h = self._hoehe_zuletzt if self._hoehe_zuletzt > 0 else self.sizeHint().height()
        return QtCore.QSize(b + rb, h)

    def setGeometry(self, rect: QtCore.QRect):
        super().setGeometry(rect)
        h = self._anordnen(rect, setzen=True)
        if h != self._hoehe_zuletzt:
            # andere Zeilenzahl: Mindesthoehe nachziehen (Elternlayout neu berechnen)
            self._hoehe_zuletzt = h
            self.invalidate()
            p = self.parentWidget()
            if p is not None:
                p.updateGeometry()
        self._breite_zuletzt = rect.width()

    # ------------------------------------------------------------ Anordnung
    def _zeilen(self, verfuegbar: int) -> List[List[_Eintrag]]:
        zeilen: List[List[_Eintrag]] = [[]]
        for g in self._gruppen():
            fuell = all(e.abstand or e.stretch_platz() for e in g)
            # Gruppe ohne Mindestbreite (z.B. ein Hinweis, der sich kuerzt) bricht nie um: sie nimmt den Rest
            nachgiebig = not fuell and all(self._min(e) == 0 for e in g)
            if zeilen[-1] and not fuell and not nachgiebig \
                    and self._breite_zeile(zeilen[-1] + g, self._min) > verfuegbar:
                zeilen.append([])
            if not zeilen[-1] and fuell:
                continue                                  # kein Abstand/Stretch am Zeilenanfang
            zeilen[-1].extend(g)
        for z in zeilen:                                  # feste Abstaende am Zeilenende weg
            while z and z[-1].abstand:
                z.pop()
        return [z for z in zeilen if z]

    def _breiten(self, z: List[_Eintrag], verfuegbar: int) -> List[int]:
        mins = [self._min(e) for e in z]
        maxs = [max(m, min(e.item.maximumSize().width(), 10 ** 6)) for e, m in zip(z, mins)]
        # wie QBoxLayout (smartSizeHint): Elemente mit Stretch wuenschen nur ihre Mindest- bzw. Wunschbreite
        wun = [min(max(max(e.breite, m) if e.stretch > 0 else self._wunsch(e), m), mx)
               for e, m, mx in zip(z, mins, maxs)]
        platz = verfuegbar - sum(self._abstaende(z))
        if sum(wun) >= platz:                             # zu eng: von der Wunsch- Richtung Mindestbreite
            fehl = sum(wun) - platz
            spiel = sum(w - m for w, m in zip(wun, mins))
            if spiel <= 0:
                return mins
            fehl = min(fehl, spiel)
            return [w - (fehl * (w - m)) // spiel for w, m in zip(wun, mins)]
        # Platz wie im QHBoxLayout verteilen: Elemente mit Stretch bekommen Breiten im Verhaeltnis ihrer
        # Stretch-Faktoren (wer dabei unter seinen Wunsch bzw. ueber sein Maximum kaeme, bleibt dort stehen);
        # ohne Stretch teilen sich die Elemente den Platz, die horizontal wachsen wollen
        breiten = list(wun)
        gew = [e.stretch for e in z]
        if not any(gew):
            gew = [1 if (e.item.expandingDirections() & QtCore.Qt.Horizontal) else 0 for e in z]
        flex = [i for i, g in enumerate(gew) if g > 0]
        frei = platz - sum(wun[i] for i in range(len(z)) if i not in flex)
        while flex:
            summe = sum(gew[i] for i in flex)
            fest = next((i for i in flex if frei * gew[i] / summe < wun[i]), None)
            if fest is None:
                fest = next((i for i in flex if frei * gew[i] / summe > maxs[i]), None)
                if fest is not None:
                    breiten[fest] = maxs[fest]
            if fest is None:
                uebertrag = 0.0                           # Rundung wie Qt: Rest an das naechste Element
                for i in flex:
                    uebertrag += frei * gew[i] / summe
                    breiten[i] = int(uebertrag + 0.5)
                    uebertrag -= breiten[i]
                break
            frei -= breiten[fest]
            flex.remove(fest)
        return breiten

    @staticmethod
    def _hoehe(e: _Eintrag, breite: int) -> int:
        if isinstance(e.item, QtWidgets.QSpacerItem):
            return 0
        h = e.item.sizeHint().height()
        if e.item.hasHeightForWidth():
            h = max(e.item.minimumSize().height(), e.item.heightForWidth(breite))
        return h

    def _anordnen(self, rect: QtCore.QRect, setzen: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        zeilen = self._zeilen(r.width())
        breiten = [self._breiten(z, r.width()) for z in zeilen]
        hoehen = [max([self._hoehe(e, b) for e, b in zip(z, bz)] or [0]) for z, bz in zip(zeilen, breiten)]
        noetig = sum(hoehen) + self._v * max(0, len(zeilen) - 1) + m.top() + m.bottom()
        if not setzen:
            return noetig
        if zeilen:
            # mehr Hoehe als noetig (Layout mit Stretch im Elternlayout): bekommt die letzte Zeile
            frei = r.height() - (noetig - m.top() - m.bottom())
            if frei > 0:
                hoehen[-1] += frei
        y = r.y()
        for z, bz, hoehe in zip(zeilen, breiten, hoehen):
            x = r.x()
            for e, b, a in zip(z, bz, self._abstaende(z)):
                x += a
                h = min(hoehe, e.item.maximumSize().height())
                e.item.setGeometry(QtCore.QRect(x, y + (hoehe - h) // 2, b, h))
                x += b
            y += hoehe + self._v
        return noetig
