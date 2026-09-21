# FASE E backup 20260920-201618

Visor dinámico de atajos (solo lectura). No se modificaron binds de Hyprland.

## Archivos creados

- shell/servicios/atajos/__init__.py
- shell/servicios/atajos/model.py
- shell/servicios/atajos/keys.py
- shell/servicios/atajos/labels.py
- shell/servicios/atajos/hyprland.py
- shell/servicios/atajos/registry.py
- shell/widgets/configuraciones/shortcuts_page.py
- shell/tests/test_shortcuts.py

## Archivos modificados

- shell/settings/schema.py          (CategoryId.ATAJOS)
- shell/widgets/configuraciones/overlay.py
- shell/widgets/configuraciones/pages.py
- shell/style.css
- shell/tests/test_settings.py

## Snapshots

- hypr/hyprland.conf
- settings.json
- files/  (copia de los fuentes tocados al cerrar la fase)

## Restaurar conf Hyprland (si hiciera falta)

```bash
cp /home/angeljoc/Documentos/Proyectos_AidyC/jugoo/backup/20260920-201618/hypr/hyprland.conf ~/.config/hypr/hyprland.conf
```
