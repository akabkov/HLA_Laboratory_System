"""Разбор CSV-файлов анализатора Luminex.

В модуле описаны dataclass-модели `LuminexAntibodyRow` и `ParsedLuminexCsv`,
а также функции `parse_luminex_csv()` и `parse_fixed_luminex_csv()`. Здесь
сосредоточена логика чтения фиксированной структуры CSV и преобразования ее
в данные для импорта, Excel-отчетов и заключений.
"""

# Реализация доступна по условиям, описанным в разделе «Получение исходного кода»:
# https://github.com/akabkov/HLA_Laboratory_System#получение-исходного-кода
