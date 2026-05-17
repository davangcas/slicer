# Reglas del Proyecto Slicer

Este documento establece los estándares operacionales y las reglas obligatorias para el funcionamiento de opencode dentro del contexto del proyecto 'Slicer'.

## ⚙️ Operación Obligatoria (Busqueda Web)
**OBLIGATORIO**: Previo al inicio de cualquier proceso o tarea, el agente debe ejecutar una búsqueda web utilizando la documentación oficial (`opencode.ai`) para identificar y asegurar todos los elementos necesarios requeridos para cumplir con el objetivo de la consulta del usuario.

## 🐍 Estándares de Codificación y Procesamiento
- **Lenguaje Principal**: Código desarrollado principalmente en Python.
- **Formateo Python**: Aplicar Ruff como formateador estándar para todo código Python.
- **Formateo JSON**: Utilizar Prettier para el formato de cualquier bloque o archivo JSON generado.
- **Robustez del Resultado**: Implementar una estrategia de reprocesamiento, ejecutando cada consulta o acción al menos 5 veces antes de emitir un resultado final.