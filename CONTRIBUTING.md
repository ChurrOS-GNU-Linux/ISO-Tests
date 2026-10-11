# Contribuir

1. Abre una rama y un pull request; no hagas push directo a `main`.
2. Mantén los perfiles anclados a archivos/configuración y arquitecturas comprobadas en ChurrOS. Si cambia `profiledef.sh`, revisa los modos y actualiza `docs/audit.md` con evidencia.
3. No declares que una ISO arranca con base solo en su tamaño, el nombre del archivo, el contenido de un loader o un timeout de QEMU.
4. Añade una prueba automatizada para cada caso positivo/negativo; fixtures y QEMU falsos son solo tests del arnés, nunca evidencia del sistema operativo.
5. Ejecuta antes del PR:

```bash
python3 -m py_compile scripts/iso_tests.py tests/test_iso_tests.py
python3 -m unittest discover -s tests -v
```

6. Si tienes una ISO real, identifica la fuente/version/hash y corre el perfil correspondiente. Conserva `report.json` y `evidence/`; no subas ISOs privadas, claves, artefactos de firmware modificables ni dumps sensibles.
7. Un test nuevo para QEMU debe probar tanto marker verdadero como timeout, salida prematura y código de error. Los procesos tienen que ser cancelables y sin red invitada.
8. Para cambios de licencia, integración entre repositorios, permisos o eventos upstream, pide aprobación a las personas mantenedoras de ChurrOS antes de cambiar el alcance.
