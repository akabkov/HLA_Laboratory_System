# Эксплуатационные скрипты HLA

Этот каталог содержит эксплуатационные скрипты для:

- зеркализации локальной файловой базы HLA в сетевую папку с помощью `robocopy`;
- отдельной зеркализации локальной папки с Word-заключениями в сетевую папку с помощью `robocopy`;
- автоматического резервного копирования PostgreSQL в локальный скрытый файл резервной копии;
- автоматического обновления локальной PostgreSQL из удалённой PostgreSQL;
- регистрации соответствующих задач в Планировщике заданий Windows.

Если скрипты берутся из собранного дистрибутива приложения, после сборки
PyInstaller 6.x они находятся в каталоге:
`dist\HLA_Laboratory_System\_internal\Scripts\`

Если приложение установлено на боевом ПК целиком в каталог
`C:\Program Files\HLA_Laboratory_System`, эти скрипты можно использовать
прямо по пути:
`C:\Program Files\HLA_Laboratory_System\_internal\Scripts`

## Назначение

- `HLA_LocalToNetwork_Mirror.ps1`:
  непрерывно отслеживает изменения в локальной файловой базе и зеркалирует их
  в сетевую папку.
- `Install-HLA-MirrorTask_AllUsers.ps1`:
  регистрирует задачу Планировщика Windows, которая запускает основной скрипт
  автоматически при старте компьютера.
- `HLA_WordConclusions_LocalToNetwork_Mirror.ps1`:
  непрерывно отслеживает изменения в локальной папке с Word-заключениями и
  зеркалирует их в сетевую папку.
- `Install-HLA-WordConclusionsMirrorTask_AllUsers.ps1`:
  регистрирует задачу Планировщика Windows, которая запускает сценарий
  зеркализации Word-заключений автоматически при старте компьютера.
- `HLA_Postgres_AutoBackup.ps1`:
  непрерывно отслеживает активность записи в PostgreSQL и обновляет скрытый
  файл резервной копии после завершения серии изменений.
- `Install-HLA-PostgresBackupTask_AllUsers.ps1`:
  регистрирует задачу Планировщика Windows, которая автоматически запускает
  скрипт автоматического резервного копирования PostgreSQL при старте компьютера.
- `HLA_Postgres_RemoteToLocal_Copy.ps1`:
  каждый час по умолчанию обновляет локальную базу PostgreSQL из удалённой
  базы PostgreSQL через `pg_dump` и `pg_restore`.
- `Install-HLA-PostgresRemoteCopyTask_AllUsers.ps1`:
  регистрирует задачу Планировщика Windows, которая автоматически запускает
  скрипт обновления локальной базы PostgreSQL при старте компьютера.

## Значения по умолчанию для зеркализации

- Локальный источник по умолчанию: `D:\HLA_Laboratory_System`
- Сетевой путь назначения по умолчанию не задан.
  Его нужно явно передавать через `-Destination`
  при ручном запуске и при установке задачи.
- Путь к основному скрипту для задачи:
  `C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_LocalToNetwork_Mirror.ps1`
- Имя задачи Планировщика: `HLA Local To Network Mirror`
- Служебные логи: `C:\ProgramData\HLA_Mirror\Logs`
- Задержка перед синхронизацией после изменений: `60` секунд
- Контрольная полная синхронизация без новых событий: `60` минут
- Если локальный `robocopy` не поддерживает часть расширенных ключей,
  скрипт автоматически пропускает неподдерживаемые опции, а при `exit code 16`
  повторяет запуск в минимальном совместимом режиме и пишет предупреждения в
  `watcher.log`

## Значения по умолчанию для зеркализации Word-заключений

- Локальный источник по умолчанию не задан.
  Его нужно явно передавать через `-Source`
  при ручном запуске и при установке задачи.
- Сетевой путь назначения по умолчанию не задан.
  Его нужно явно передавать через `-Destination`
  при ручном запуске и при установке задачи.
- Путь к основному скрипту для задачи:
  `C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_WordConclusions_LocalToNetwork_Mirror.ps1`
- Имя задачи Планировщика: `HLA Word Conclusions Mirror`
- Служебные логи: `C:\ProgramData\HLA_WordConclusions_Mirror\Logs`
- Задержка перед синхронизацией после изменений: `60` секунд
- Контрольная полная синхронизация без новых событий: `60` минут
- Если локальный `robocopy` не поддерживает часть расширенных ключей,
  скрипт автоматически пропускает неподдерживаемые опции, а при `exit code 16`
  повторяет запуск в минимальном совместимом режиме и пишет предупреждения в
  `watcher.log`

## Значения по умолчанию для автоматического резервного копирования PostgreSQL

- Хост PostgreSQL: `localhost`
- Порт PostgreSQL: `5432`
- База данных: `hla_db`
- Пользователь PostgreSQL: `postgres`
- Пароль PostgreSQL: `0`
- Целевой файл резервной копии: `D:\HLA_Laboratory_System\hla_postgres_backup.dump`
- Интервал опроса активности БД: `15` секунд
- Пауза тишины перед резервным копированием после записи: `60` секунд
- Контрольное полное резервное копирование даже без новых событий: `60` минут
- Путь к основному скрипту для задачи:
  `C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_AutoBackup.ps1`
- Имя задачи Планировщика: `HLA PostgreSQL Auto Backup`
- Служебные логи: `C:\ProgramData\HLA_PostgresBackup\Logs`

## Значения по умолчанию для копирования удалённой PostgreSQL в локальную PostgreSQL

Для основного скрипта `HLA_Postgres_RemoteToLocal_Copy.ps1` используются
следующие значения:

- Удалённый хост PostgreSQL: нужно явно передать через `-RemoteDbHost`
- Удалённый порт PostgreSQL: `5432`
- Удалённая база данных: `hla_db`
- Удалённый пользователь PostgreSQL: нужно явно передать через `-RemoteDbUser`
- Локальный хост PostgreSQL: `localhost`
- Локальный порт PostgreSQL: `5432`
- Локальная база данных: `hla_db_remote`
- Локальный пользователь PostgreSQL: `postgres`
- Локальный пароль PostgreSQL: `0`
- Интервал обновления локальной базы: `60` минут
- Интервал повторной попытки после ошибки: `15` минут
- Рабочий каталог временных файлов резервной копии:
  `C:\ProgramData\HLA_PostgresRemoteCopy\Work`
- Путь к основному скрипту для задачи:
  `C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_RemoteToLocal_Copy.ps1`
- Имя задачи Планировщика: `HLA PostgreSQL Remote To Local Copy`
- Служебные логи: `C:\ProgramData\HLA_PostgresRemoteCopy\Logs`

Если фактические имена удалённой или локальной базы отличаются от этих
значений, их нужно явно задать параметрами `-RemoteDbName` и `-LocalDbName`.

## Общая философия настройки

Эксплуатационные сценарии в этом каталоге настраиваются по одной схеме:

- есть основной длительно работающий скрипт-наблюдатель и отдельный установщик задачи
  Планировщика Windows;
- для боевого ПК рекомендуется не править `.ps1` под каждую площадку,
  а передавать значения, зависящие от конкретной площадки, через параметры запуска;
- для зеркализации файловой базы `Install-HLA-MirrorTask_AllUsers.ps1`
  принимает обязательный `-Destination`, опциональный `-Source`
  и использует `-ScriptArguments` только для дополнительных флагов
  основного скрипта;
- для зеркализации Word-заключений
  `Install-HLA-WordConclusionsMirrorTask_AllUsers.ps1`
  принимает обязательные `-Source` и `-Destination`
  и использует `-ScriptArguments` только для дополнительных флагов
  основного скрипта;
- для обоих сценариев зеркализации рекомендуемый порядок одинаковый:
  сначала `-ListOnly`, потом `-RunOnce`, потом регистрация задачи,
  потом `Start-ScheduledTask`, потом проверка логов;
- задача Планировщика создаётся одна на весь компьютер и работает под одной
  постоянной учётной записью;
- `whoami` удобно использовать только тогда, когда текущий пользователь
  административной сессии и есть тот самый постоянный аккаунт задачи;
- сценарии разделены по назначению:
  файловая база зеркалируется в сеть, папка с Word-заключениями может
  зеркалироваться в отдельную сетевую папку, а локальный файл резервной копии PostgreSQL
  `D:\HLA_Laboratory_System\hla_postgres_backup.dump` хранится внутри
  локальной файловой базы и уезжает в сетевое зеркало вместе с ней.
- копирование удалённой PostgreSQL в локальную PostgreSQL является отдельным
  сценарием: оно не создаёт файл резервной копии, а обновляет локальную базу
  через временную базу и последующую замену имени.

## Важные предупреждения

- Скрипт использует `robocopy /MIR`.
  Это режим зеркализации, а не обычного копирования.
- Для рабочего внедрения рекомендуется не редактировать
  mirror-скрипты под каждый компьютер.
  Для `HLA_LocalToNetwork_Mirror.ps1` локальный путь `$Source`
  можно не указывать, если используется стандартный
  `D:\HLA_Laboratory_System`, а `$Destination` нужно всегда
  передавать явно.
  Для `HLA_WordConclusions_LocalToNetwork_Mirror.ps1`
  и `$Source`, и `$Destination` нужно всегда передавать явно.
- Все объекты, существующие только в сетевой папке, но отсутствующие в
  локальном источнике, будут удаляться из зеркала.
- Сетевую папку назначения нужно использовать только как зеркало.
  Нельзя вручную хранить в ней отдельные рабочие файлы.
- Сценарий зеркализации Word-заключений зеркалирует файлы как есть.
  Он не преобразует `.doc` / `.docx` в PDF и не заменяет
  логику импорта в самом приложении.
- На старых сборках Windows `robocopy` может не поддерживать часть
  расширенных ключей, включая `/IORATE`, `/THRESHOLD`, `/COMPRESS`,
  `/MT`, `/DCOPY`, `/XJ`, `/IT` или `/Z`. Основной скрипт теперь сам
  определяет поддержку этих ключей, пропускает их при необходимости и
  автоматически делает повторный запуск в минимальном совместимом режиме.
- Любое изменение локального корня файловой базы в приложении или при
  развёртывании требует синхронного обновления параметра `$Source`
  в параметрах запуска задачи зеркализации.
- Любое изменение локального пути папки с Word-заключениями или её
  сетевого зеркала требует синхронного обновления параметров
  `$Source` и `$Destination` в параметрах запуска соответствующей задачи.
- Если основной скрипт установлен не по пути по умолчанию, нужно также
  передать корректный `-SyncScriptPath` при регистрации
  соответствующей задачи.

## Предварительные условия

- Если используется собранный дистрибутив, исходные `.ps1` файлы взяты из:
  `dist\HLA_Laboratory_System\_internal\Scripts`
- На боевом ПК приложение установлено в:
  `C:\Program Files\HLA_Laboratory_System`
- Скрипты доступны по пути:
  `C:\Program Files\HLA_Laboratory_System\_internal\Scripts`
- Локальная файловая база доступна по пути:
  `D:\HLA_Laboratory_System`
- У выбранного пользователя для задачи есть доступ:
  на чтение к локальному источнику и на запись в сетевую папку
  `\\ПУТЬ_К_ЗЕРКАЛУ_НА_ВАШЕМ_ПК_ИЛИ_В_ВАШЕЙ_СЕТИ\HLA_Laboratory_System`
- Команды ниже выполняются из PowerShell от имени администратора

## Зеркализация файловой базы

Ниже приведён пошаговый порядок внедрения сценария зеркализации.

### 1. Проверить тестовый запуск без копирования

Выполнить:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_LocalToNetwork_Mirror.ps1" -ListOnly -Destination "\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\HLA_Laboratory_System"
```

Если локальный источник отличается от `D:\HLA_Laboratory_System`,
добавьте параметр `-Source "ВАШ_ЛОКАЛЬНЫЙ_ПУТЬ"`.

После этого проверьте:

- `C:\ProgramData\HLA_Mirror\Logs\watcher.log`
- `C:\ProgramData\HLA_Mirror\Logs\listonly_YYYYMMDD.log`

Ожидаемый результат:

- скрипт отработал без ошибок;
- в логе нет неожиданных операций над нужными файлами;
- список изменений соответствует ожидаемому зеркалу.

### 2. Выполнить первую реальную разовую синхронизацию

После успешного `-ListOnly` выполните один реальный проход зеркализации:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_LocalToNetwork_Mirror.ps1" -RunOnce -Destination "\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\HLA_Laboratory_System"
```

Если локальный источник отличается от `D:\HLA_Laboratory_System`,
добавьте параметр `-Source "ВАШ_ЛОКАЛЬНЫЙ_ПУТЬ"`.

После этого проверьте:

- `C:\ProgramData\HLA_Mirror\Logs\watcher.log`
- `C:\ProgramData\HLA_Mirror\Logs\robocopy_YYYYMMDD.log`

Ожидаемый результат:

- выполнена одна реальная синхронизация;
- в сетевом зеркале появились ожидаемые данные;
- в сетевом зеркале появились все ожидаемые файлы, включая актуальный
  `hla_postgres_backup.dump`, если он уже существует в исходной папке.

### 3. Определить пользователя для Планировщика

В той же административной сессии выполните:

```powershell
$env:COMPUTERNAME
whoami
```

Для параметра `-TaskUser` использовать полный логон пользователя, например:

- `PCNAME\SomeUser`
- `DOMAIN\hla_sync`

Рекомендуется использовать постоянную учётную запись, от имени которой
гарантирован доступ к сетевой папке.

Если нужно быстро зарегистрировать одну общую задачу для всего ПК под текущим
пользователем, можно использовать `whoami`. В таком режиме задача всё равно
будет одна на весь компьютер, но всегда будет работать от имени того
пользователя, под которым вы открыли административный PowerShell.

### 4. Зарегистрировать задачу Планировщика

Выполнить:

```powershell
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-MirrorTask_AllUsers.ps1" -TaskUser "ИМЯ_КОМПЬЮТЕРА_ИЛИ_ДОМЕНА\ИмяПользователя" -Destination "\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\HLA_Laboratory_System"
```

Скрипт запросит пароль указанного пользователя и создаст задачу
`HLA Local To Network Mirror`.

Если локальный источник отличается от `D:\HLA_Laboratory_System`,
передайте его явно:

```powershell
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-MirrorTask_AllUsers.ps1" -TaskUser "ИМЯ_КОМПЬЮТЕРА_ИЛИ_ДОМЕНА\ИмяПользователя" -Source "D:\ВАШ_ЛОКАЛЬНЫЙ_КОРЕНЬ" -Destination "\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\HLA_Laboratory_System"
```

Если основной скрипт лежит не в стандартной папке, использовать:

```powershell
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-MirrorTask_AllUsers.ps1" -TaskUser "ИМЯ_КОМПЬЮТЕРА_ИЛИ_ДОМЕНА\ИмяПользователя" -SyncScriptPath "ПОЛНЫЙ_ПУТЬ_К_HLA_LocalToNetwork_Mirror.ps1" -Destination "\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\HLA_Laboratory_System"
```

При запуске installer-скриптов из PowerShell рекомендуется вызывать их прямо
через `&`, а не через `powershell.exe -File`, чтобы параметры
`-Source`, `-Destination` и при необходимости `-ScriptArguments`
с путями и внутренними кавычками передавались без искажений.

Если нужно передать дополнительные флаги основному скрипту, например
`-UseCompression`, используйте `-ScriptArguments`, например:

```powershell
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-MirrorTask_AllUsers.ps1" -TaskUser "ИМЯ_КОМПЬЮТЕРА_ИЛИ_ДОМЕНА\ИмяПользователя" -Destination "\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\HLA_Laboratory_System" -ScriptArguments '-UseCompression'
```

### Готовый набор команд через `whoami`

Перед запуском блока ниже обязательно проверьте и при необходимости замените:

- `$Destination = '\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\HLA_Laboratory_System'`
  на ваш реальный сетевой путь зеркала;
- если локальный источник отличается от `D:\HLA_Laboratory_System`,
  добавьте в команды ниже параметр `-Source "ВАШ_ЛОКАЛЬНЫЙ_ПУТЬ"`;
- если задача должна работать не от текущего пользователя,
  замените строку `$TaskUser = (whoami).Trim()` на нужную учётную запись.

Команды:

```powershell
$ScriptDir = 'C:\Program Files\HLA_Laboratory_System\_internal\Scripts'
$TaskUser = (whoami).Trim()
$Destination = '\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\HLA_Laboratory_System'

Test-Path 'D:\HLA_Laboratory_System'
Test-Path $Destination

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ScriptDir\HLA_LocalToNetwork_Mirror.ps1" -ListOnly -Destination $Destination
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ScriptDir\HLA_LocalToNetwork_Mirror.ps1" -RunOnce -Destination $Destination

& "$ScriptDir\Install-HLA-MirrorTask_AllUsers.ps1" -TaskUser $TaskUser -Destination $Destination

Start-ScheduledTask -TaskName 'HLA Local To Network Mirror'
Get-ScheduledTask -TaskName 'HLA Local To Network Mirror'
Get-ScheduledTaskInfo -TaskName 'HLA Local To Network Mirror'
Get-Content 'C:\ProgramData\HLA_Mirror\Logs\watcher.log' -Tail 50
```

### 5. Запустить задачу вручную после регистрации

Выполнить:

```powershell
Start-ScheduledTask -TaskName "HLA Local To Network Mirror"
```

### 6. Проверить, что задача создана и реально стартовала

Выполнить:

```powershell
Get-ScheduledTask -TaskName "HLA Local To Network Mirror"
Get-ScheduledTaskInfo -TaskName "HLA Local To Network Mirror"
```

Нужно убедиться, что:

- задача зарегистрирована без ошибок;
- используется ожидаемая учётная запись;
- задача перешла в рабочее состояние после запуска.

### 7. Проверить логи работы

Проверить файлы:

- `C:\ProgramData\HLA_Mirror\Logs\watcher.log`
- `C:\ProgramData\HLA_Mirror\Logs\robocopy_YYYYMMDD.log`

В `watcher.log` должны появиться записи о:

- запуске наблюдателя;
- первом запуске синхронизации;
- дальнейших синхронизациях по событиям или по контрольному таймеру.

### 8. Выполнить функциональную проверку

- выполнить импорт в приложении;
- подождать не менее `60` секунд после завершения файловых изменений;
- убедиться, что новые файлы появились в сетевом зеркале;
- при необходимости дополнительно проверить повторную синхронизацию
  по контрольному интервалу `FullResyncMinutes`.

## Эксплуатационные примечания

- Скрипт защищён от запуска второго экземпляра на том же компьютере.
- Если исходная папка в момент старта недоступна, основной скрипт завершится
  с ошибкой и будет перезапущен Планировщиком согласно его настройкам.
- Планировщик регистрирует запуск при старте системы и повторные попытки при
  сбое, но не заменяет корректную настройку сетевого доступа и путей.
- В `ListOnly`-режиме реальная синхронизация не выполняется.
- В `RunOnce`-режиме выполняется один реальный проход синхронизации и выход.
- Для рабочего внедрения рекомендуется одинаковый цикл:
  `ListOnly -> RunOnce -> регистрация задачи -> ручной старт -> проверка логов`.

## Когда обязательно нужно обновить настройки скриптов

Нужно пересмотреть параметры скриптов, если изменилось хотя бы одно из перечисленного ниже:

- локальный корень файловой базы;
- путь к сетевому зеркалу;
- путь размещения самих `.ps1` файлов;
- учётная запись, от имени которой запускается задача;
- права доступа к локальному источнику или сетевой папке.

В таких случаях проверить:

- `-Destination` в команде регистрации задачи
- при необходимости `-Source`, если локальный корень отличается
  от `D:\HLA_Laboratory_System`
- при использовании дополнительных флагов основной задачи
  `-ScriptArguments`
- при необходимости `-SyncScriptPath`
  в команде регистрации задачи

## Краткий чек-лист перед рабочим запуском

- скрипты доступны в `C:\Program Files\HLA_Laboratory_System\_internal\Scripts`
- локальный путь `D:\HLA_Laboratory_System` существует
- сетевой путь доступен на запись
- тестовый прогон `-ListOnly` выполнен без сюрпризов
- `-RunOnce` отработал ожидаемо
- задача зарегистрирована от корректного пользователя
- после ручного запуска появились записи в `watcher.log`
- тестовый импорт из приложения дошёл до сетевого зеркала

## Зеркализация Word-заключений

Ниже приведён отдельный сценарий для непрерывной зеркализации локальной папки,
в которой хранятся Word-заключения, в сетевую папку.

Скрипт `HLA_WordConclusions_LocalToNetwork_Mirror.ps1` зеркалирует файлы как
обычное содержимое папки и не выполняет преобразование `.doc` / `.docx` в PDF.

### Предварительные условия для зеркализации Word-заключений

- если используется собранный дистрибутив, исходные `.ps1` файлы взяты из:
  `dist\HLA_Laboratory_System\_internal\Scripts`
- на боевом ПК приложение установлено в:
  `C:\Program Files\HLA_Laboratory_System`
- скрипты доступны по пути:
  `C:\Program Files\HLA_Laboratory_System\_internal\Scripts`
- локальная папка с Word-заключениями известна заранее
  и доступна по фактическому пути вида:
  `D:\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ`
- сетевая папка зеркала известна заранее
  и доступна по фактическому пути вида:
  `\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ`
- у выбранного пользователя для задачи есть доступ:
  на чтение к локальной папке с Word-заключениями
  и на запись в сетевую папку зеркала
- команды ниже выполняются из PowerShell от имени администратора

### 1. Проверить тестовый запуск без копирования

Выполнить:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_WordConclusions_LocalToNetwork_Mirror.ps1" -ListOnly -Source "D:\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ" -Destination "\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ"
```

После этого проверьте:

- `C:\ProgramData\HLA_WordConclusions_Mirror\Logs\watcher.log`
- `C:\ProgramData\HLA_WordConclusions_Mirror\Logs\listonly_YYYYMMDD.log`

Ожидаемый результат:

- скрипт отработал без ошибок;
- в логе нет неожиданных операций над нужными файлами;
- список изменений соответствует ожидаемому зеркалу папки с Word-заключениями.

### 2. Выполнить первую реальную разовую синхронизацию

После успешного `-ListOnly` выполните один реальный проход зеркализации:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_WordConclusions_LocalToNetwork_Mirror.ps1" -RunOnce -Source "D:\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ" -Destination "\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ"
```

После этого проверьте:

- `C:\ProgramData\HLA_WordConclusions_Mirror\Logs\watcher.log`
- `C:\ProgramData\HLA_WordConclusions_Mirror\Logs\robocopy_YYYYMMDD.log`

Ожидаемый результат:

- выполнена одна реальная синхронизация;
- в сетевом зеркале появились ожидаемые Word-заключения и связанные файлы;
- структура папок в зеркале соответствует локальному источнику.

### 3. Определить пользователя для Планировщика

В той же административной сессии выполните:

```powershell
$env:COMPUTERNAME
whoami
```

Для параметра `-TaskUser` использовать полный логон пользователя, например:

- `PCNAME\SomeUser`
- `DOMAIN\hla_sync`

Рекомендуется использовать постоянную учётную запись, от имени которой
гарантирован доступ к локальной папке с Word-заключениями и сетевой папке зеркала.

Если нужно быстро зарегистрировать одну общую задачу для всего ПК под текущим
пользователем, можно использовать `whoami`. В таком режиме задача всё равно
будет одна на весь компьютер, но всегда будет работать от имени того
пользователя, под которым вы открыли административный PowerShell.

### 4. Зарегистрировать задачу Планировщика

Выполнить:

```powershell
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-WordConclusionsMirrorTask_AllUsers.ps1" -TaskUser "ИМЯ_КОМПЬЮТЕРА_ИЛИ_ДОМЕНА\ИмяПользователя" -Source "D:\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ" -Destination "\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ"
```

Скрипт запросит пароль указанного пользователя и создаст задачу
`HLA Word Conclusions Mirror`.

Если основной скрипт лежит не в стандартной папке, использовать:

```powershell
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-WordConclusionsMirrorTask_AllUsers.ps1" -TaskUser "ИМЯ_КОМПЬЮТЕРА_ИЛИ_ДОМЕНА\ИмяПользователя" -SyncScriptPath "ПОЛНЫЙ_ПУТЬ_К_HLA_WordConclusions_LocalToNetwork_Mirror.ps1" -Source "D:\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ" -Destination "\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ"
```

При запуске installer-скриптов из PowerShell рекомендуется вызывать их прямо
через `&`, а не через `powershell.exe -File`, чтобы параметры
`-Source`, `-Destination` и при необходимости `-ScriptArguments`
с путями и внутренними кавычками передавались без искажений.

Если нужно передать дополнительные флаги основному скрипту, например
`-UseCompression`, используйте `-ScriptArguments`, например:

```powershell
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-WordConclusionsMirrorTask_AllUsers.ps1" -TaskUser "ИМЯ_КОМПЬЮТЕРА_ИЛИ_ДОМЕНА\ИмяПользователя" -Source "D:\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ" -Destination "\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ" -ScriptArguments '-UseCompression'
```

### Готовый набор команд через `whoami`

Перед запуском блока ниже обязательно проверьте и при необходимости замените:

- `$Source = 'D:\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ'`
  на ваш реальный локальный путь к папке с Word-заключениями;
- `$Destination = '\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ'`
  на ваш реальный сетевой путь зеркала;
- если задача должна работать не от текущего пользователя,
  замените строку `$TaskUser = (whoami).Trim()` на нужную учётную запись.

Команды:

```powershell
$ScriptDir = 'C:\Program Files\HLA_Laboratory_System\_internal\Scripts'
$TaskUser = (whoami).Trim()
$Source = 'D:\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ'
$Destination = '\\ПУТЬ_К_ЗЕРКАЛУ_В_ВАШЕЙ_СЕТИ\ПАПКА_С_WORD_ЗАКЛЮЧЕНИЯМИ'

Test-Path $Source
Test-Path $Destination

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ScriptDir\HLA_WordConclusions_LocalToNetwork_Mirror.ps1" -ListOnly -Source $Source -Destination $Destination
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ScriptDir\HLA_WordConclusions_LocalToNetwork_Mirror.ps1" -RunOnce -Source $Source -Destination $Destination

& "$ScriptDir\Install-HLA-WordConclusionsMirrorTask_AllUsers.ps1" -TaskUser $TaskUser -Source $Source -Destination $Destination

Start-ScheduledTask -TaskName 'HLA Word Conclusions Mirror'
Get-ScheduledTask -TaskName 'HLA Word Conclusions Mirror'
Get-ScheduledTaskInfo -TaskName 'HLA Word Conclusions Mirror'
Get-Content 'C:\ProgramData\HLA_WordConclusions_Mirror\Logs\watcher.log' -Tail 50
```

### 5. Запустить задачу вручную после регистрации

Выполнить:

```powershell
Start-ScheduledTask -TaskName "HLA Word Conclusions Mirror"
```

### 6. Проверить, что задача создана и реально стартовала

Выполнить:

```powershell
Get-ScheduledTask -TaskName "HLA Word Conclusions Mirror"
Get-ScheduledTaskInfo -TaskName "HLA Word Conclusions Mirror"
```

Нужно убедиться, что:

- задача зарегистрирована без ошибок;
- используется ожидаемая учётная запись;
- задача перешла в рабочее состояние после запуска.

### 7. Проверить логи работы

Проверить файлы:

- `C:\ProgramData\HLA_WordConclusions_Mirror\Logs\watcher.log`
- `C:\ProgramData\HLA_WordConclusions_Mirror\Logs\robocopy_YYYYMMDD.log`

В `watcher.log` должны появиться записи о:

- запуске наблюдателя;
- первом запуске синхронизации;
- дальнейших синхронизациях по событиям или по контрольному таймеру.

### 8. Выполнить функциональную проверку

- поместить тестовый `.doc` или `.docx` файл в локальную папку источника;
- подождать не менее `60` секунд после завершения файловых изменений;
- убедиться, что файл появился в сетевом зеркале;
- при необходимости проверить обновление изменённого файла
  и зеркализацию удаления лишних объектов.

## Эксплуатационные примечания для зеркализации Word-заключений

- Скрипт защищён от запуска второго экземпляра на том же компьютере.
- Если исходная папка в момент старта недоступна, основной скрипт завершится
  с ошибкой и будет перезапущен Планировщиком согласно его настройкам.
- Планировщик регистрирует запуск при старте системы и повторные попытки при
  сбое, но не заменяет корректную настройку сетевого доступа и путей.
- В `ListOnly`-режиме реальная синхронизация не выполняется.
- В `RunOnce`-режиме выполняется один реальный проход синхронизации и выход.
- Для рабочего внедрения рекомендуется одинаковый цикл:
  `ListOnly -> RunOnce -> регистрация задачи -> ручной старт -> проверка логов`.
- Так как используется `robocopy /MIR`, сетевую папку назначения нужно
  использовать только как зеркало.

## Когда обязательно нужно обновить настройки зеркализации Word-заключений

Нужно пересмотреть параметры скриптов, если изменилось хотя бы одно из перечисленного ниже:

- локальный путь папки с Word-заключениями;
- путь к сетевому зеркалу;
- путь размещения самих `.ps1` файлов;
- учётная запись, от имени которой запускается задача;
- права доступа к локальному источнику или сетевой папке.

В таких случаях проверить:

- `-Source` в команде регистрации задачи
- `-Destination` в команде регистрации задачи
- при использовании дополнительных флагов основной задачи
  `-ScriptArguments`
- при необходимости `-SyncScriptPath`
  в команде регистрации задачи

## Краткий чек-лист перед рабочим запуском зеркализации Word-заключений

- скрипты доступны в `C:\Program Files\HLA_Laboratory_System\_internal\Scripts`
- локальная папка с Word-заключениями существует
- сетевой путь доступен на запись
- тестовый прогон `-ListOnly` выполнен без сюрпризов
- `-RunOnce` отработал ожидаемо
- задача зарегистрирована от корректного пользователя
- после ручного запуска появились записи в `watcher.log`
- тестовый Word-файл дошёл до сетевого зеркала

## Автоматическое резервное копирование PostgreSQL

Ниже описан отдельный рабочий сценарий для локального резервного
копирования PostgreSQL на боевом ПК.

Скрипт `HLA_Postgres_AutoBackup.ps1` работает как фоновый скрипт-наблюдатель:

- периодически опрашивает статистику записи в PostgreSQL;
- ждёт завершения серии изменений (`DebounceSeconds`);
- запускает `pg_dump` и обновляет один скрытый файл резервной копии;
- дополнительно выполняет контрольное резервное копирование по таймеру `FullBackupMinutes`;
- пишет лог работы в `C:\ProgramData\HLA_PostgresBackup\Logs\backup_watcher.log`.

### Как именно работает отслеживание изменений

- Скрипт не встраивается в приложение и не требует правки Python-кода.
- Он ориентируется на статистику пользовательских таблиц PostgreSQL
  (`pg_stat_user_tables`), то есть реагирует на реальные записи в БД.
- Резервное копирование выполняется не мгновенно после каждой отдельной SQL-операции,
  а после завершения серии изменений и выдержки паузы `DebounceSeconds`.
- Такой режим специально выбран для рабочего сценария, чтобы не запускать `pg_dump`
  по несколько раз подряд во время одного импорта или серии быстрых правок.

### Важные предупреждения для автоматического резервного копирования

- Перед настройкой сценария автоматического резервного копирования на боевом ПК рекомендуется заранее добавить
  каталог PostgreSQL 18 `bin` в системную переменную `PATH`:
  `C:\Program Files\PostgreSQL\18\bin`.
  Это особенно желательно для задачи Планировщика Windows, чтобы
  `pg_dump.exe` и `psql.exe` были доступны без дополнительных параметров.
- На боевом ПК должны быть доступны `pg_dump.exe` и `psql.exe`.
  В проекте ориентируемся на PostgreSQL 18, поэтому чаще всего они лежат в
  `C:\Program Files\PostgreSQL\18\bin`.
- Скрипт сначала ищет эти утилиты в `PATH`, а если не находит,
  пытается автоматически обнаружить их в стандартных каталогах
  `C:\Program Files\PostgreSQL\*\bin`.
- Если на компьютере установлено несколько версий PostgreSQL,
  скрипт автоматически выберет самую новую из найденных.
- Если PostgreSQL установлен в нестандартный путь, нужно передать
  параметр `-PgBinDir`.
- Скрипт поддерживает один актуальный файл резервной копии, а не историю копий.
  Каждая новая успешная резервная копия заменяет предыдущую.
- Файл резервной копии помечается атрибутом `Hidden`.
  В проводнике Windows он будет виден только если включён показ скрытых файлов.
- По умолчанию база данных для резервного копирования: `hla_db`.
  Если на боевом ПК реально используется другое имя базы
  (например `hla_db_after` или `hla_db_before`), это обязательно нужно
  явно указать через параметр `-DbName`.

### Предварительные условия для автоматического резервного копирования

- приложение установлено на боевой ПК, например в:
  `C:\Program Files\HLA_Laboratory_System`
- скрипты доступны по пути:
  `C:\Program Files\HLA_Laboratory_System\_internal\Scripts`
- локальная файловая база доступна по пути:
  `D:\HLA_Laboratory_System`
- локальный пользователь или сервисная учётная запись, от имени которой будет
  работать задача, имеет право:
  на запуск PowerShell, на чтение PostgreSQL и на запись в
  `D:\HLA_Laboratory_System`
- PostgreSQL 18 установлен локально или доступен по указанным параметрам
  подключения
- команды ниже выполняются из PowerShell от имени администратора

### 1. Проверить, где находятся `pg_dump.exe` и `psql.exe`

Перед этой проверкой рекомендуется добавить
`C:\Program Files\PostgreSQL\18\bin` в системный `PATH` и открыть новую
сессию PowerShell.

Сначала на боевом ПК откройте PowerShell от имени администратора и выполните:

```powershell
Get-Command pg_dump.exe
Get-Command psql.exe
```

Если обе команды отработали успешно, можно использовать настройки по умолчанию.

Если команды не найдены, но PostgreSQL установлен, проверьте стандартный каталог:

```powershell
Get-ChildItem "C:\Program Files\PostgreSQL" -Directory
```

После этого определите фактический путь к `bin`, например:

```text
C:\Program Files\PostgreSQL\18\bin
```

Этот путь затем нужно будет передать в параметре `-PgBinDir`.

### 2. При необходимости уточнить реальное имя БД

По умолчанию скрипт резервного копирования использует:

- `DbHost=localhost`
- `DbPort=5432`
- `DbName=hla_db`
- `DbUser=postgres`
- `DbPassword=0`

Если на боевом ПК реально используется другое имя базы, например
`hla_db_after`, сразу решите, каким способом вы будете его задавать:

- либо запускать скрипт с параметром `-DbName`;
- либо передать системную переменную окружения `HLA_APP_DB_NAME`;
- либо указать нужное значение в `-ScriptArguments` при регистрации задачи.

На практике для рабочего сценария обычно проще и прозрачнее передать это прямо
в `-ScriptArguments`.

### 3. Выполнить тестовый запуск без реального резервного копирования

Это эквивалент проверки конфигурации.

Если PostgreSQL tools находятся в `PATH`, выполните:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_AutoBackup.ps1" -ListOnly
```

Если нужен явный путь к `bin`, выполните:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_AutoBackup.ps1" -ListOnly -PgBinDir "C:\Program Files\PostgreSQL\18\bin"
```

Если у вас нестандартное имя базы, добавьте, например:

```powershell
-DbName "hla_db_after"
```

После этого проверьте лог:

- `C:\ProgramData\HLA_PostgresBackup\Logs\backup_watcher.log`

Ожидаемый результат:

- `pg_dump.exe` и `psql.exe` найдены;
- каталог для файла резервной копии доступен на запись;
- подключение к PostgreSQL прошло успешно;
- в логе есть запись `ListOnly completed successfully. No real backup was created.`
  или эквивалентное сообщение об успешной проверке без создания реального файла резервной копии.

### 4. Выполнить первое реальное резервное копирование вручную

После успешного `-ListOnly` выполните разовое реальное резервное копирование:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_AutoBackup.ps1" -RunOnce
```

Если нужны дополнительные параметры, пример:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_AutoBackup.ps1" -RunOnce -DbName "hla_db_after" -PgBinDir "C:\Program Files\PostgreSQL\18\bin"
```

После завершения проверьте:

- лог `C:\ProgramData\HLA_PostgresBackup\Logs\backup_watcher.log`;
- наличие файла
  `D:\HLA_Laboratory_System\hla_postgres_backup.dump`

Так как файл скрытый, удобнее проверить его так:

```powershell
Get-Item -Force "D:\HLA_Laboratory_System\hla_postgres_backup.dump"
```

Если команда показывает файл, а в логе есть запись вида
`Backup file updated: ...`, значит первая рабочая резервная копия создана успешно.

### 5. Определить пользователя для задачи Планировщика

В той же административной сессии выполните:

```powershell
$env:COMPUTERNAME
whoami
```

Для параметра `-TaskUser` используйте полный логон пользователя, например:

- `PCNAME\SomeUser`
- `DOMAIN\hla_backup`

Рекомендуется использовать постоянную учётную запись, под которой:

- доступен локальный PostgreSQL;
- есть запись в `D:\HLA_Laboratory_System`;
- пароль не меняется слишком часто.

Если нужно быстро зарегистрировать одну общую задачу для всего ПК под текущим
пользователем, можно использовать `whoami`. В таком режиме задача всё равно
будет одна на весь компьютер, но всегда будет работать от имени того
пользователя, под которым вы открыли административный PowerShell.

### 6. Зарегистрировать задачу Планировщика для всех пользователей ПК

Вариант с параметрами по умолчанию:

```powershell
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-PostgresBackupTask_AllUsers.ps1" -TaskUser "ИМЯ_КОМПЬЮТЕРА_ИЛИ_ДОМЕНА\ИмяПользователя"
```

Скрипт запросит пароль указанного пользователя и создаст задачу:

- `HLA PostgreSQL Auto Backup`

Если нужно передать нестандартное имя БД и путь к PostgreSQL `bin`,
используйте `-ScriptArguments`, например:

```powershell
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-PostgresBackupTask_AllUsers.ps1" -TaskUser "ИМЯ_КОМПЬЮТЕРА_ИЛИ_ДОМЕНА\ИмяПользователя" -ScriptArguments '-DbName "hla_db_after" -PgBinDir "C:\Program Files\PostgreSQL\18\bin"'
```

Если основной скрипт резервного копирования лежит не по стандартному пути, используйте:

```powershell
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-PostgresBackupTask_AllUsers.ps1" -TaskUser "ИМЯ_КОМПЬЮТЕРА_ИЛИ_ДОМЕНА\ИмяПользователя" -BackupScriptPath "ПОЛНЫЙ_ПУТЬ_К_HLA_Postgres_AutoBackup.ps1"
```

При запуске installer-скриптов из PowerShell рекомендуется вызывать их прямо
через `&`, а не через `powershell.exe -File`, чтобы строка `-ScriptArguments`
с внутренними кавычками и путями вроде `C:\Program Files\...` передавалась
корректно.

### Готовый набор команд через `whoami`

Перед запуском блока ниже обязательно проверьте и при необходимости замените:

- `$DbName = 'РЕАЛЬНОЕ_ИМЯ_РАБОЧЕЙ_БАЗЫ'`;
- при необходимости `$PgBin = 'C:\Program Files\PostgreSQL\18\bin'`
  на фактический путь к PostgreSQL 18 `bin`;
- если задача должна работать не от текущего пользователя,
  замените строку `$TaskUser = (whoami).Trim()` на нужную учётную запись.

Команды:

```powershell
$ScriptDir = 'C:\Program Files\HLA_Laboratory_System\_internal\Scripts'
$PgBin = 'C:\Program Files\PostgreSQL\18\bin'
$DbName = 'РЕАЛЬНОЕ_ИМЯ_РАБОЧЕЙ_БАЗЫ'
$TaskUser = (whoami).Trim()
$ScriptArguments = '-DbName "' + $DbName + '" -PgBinDir "' + $PgBin + '"'

$machinePath = [Environment]::GetEnvironmentVariable('Path','Machine')
if (($machinePath -split ';') -notcontains $PgBin) {
    [Environment]::SetEnvironmentVariable('Path', ($machinePath.TrimEnd(';') + ';' + $PgBin), 'Machine')
}
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')

Get-Command pg_dump.exe
Get-Command psql.exe

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ScriptDir\HLA_Postgres_AutoBackup.ps1" -ListOnly -DbName $DbName -PgBinDir $PgBin
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ScriptDir\HLA_Postgres_AutoBackup.ps1" -RunOnce -DbName $DbName -PgBinDir $PgBin

Get-Item -Force 'D:\HLA_Laboratory_System\hla_postgres_backup.dump'
Get-Content 'C:\ProgramData\HLA_PostgresBackup\Logs\backup_watcher.log' -Tail 50

& "$ScriptDir\Install-HLA-PostgresBackupTask_AllUsers.ps1" -TaskUser $TaskUser -ScriptArguments $ScriptArguments

Start-ScheduledTask -TaskName 'HLA PostgreSQL Auto Backup'
Get-ScheduledTask -TaskName 'HLA PostgreSQL Auto Backup'
Get-ScheduledTaskInfo -TaskName 'HLA PostgreSQL Auto Backup'
```

### 7. Запустить задачу вручную сразу после регистрации

Выполните:

```powershell
Start-ScheduledTask -TaskName "HLA PostgreSQL Auto Backup"
```

### 8. Проверить, что задача реально создана и стартовала

Выполните:

```powershell
Get-ScheduledTask -TaskName "HLA PostgreSQL Auto Backup"
Get-ScheduledTaskInfo -TaskName "HLA PostgreSQL Auto Backup"
```

Нужно убедиться, что:

- задача зарегистрирована без ошибок;
- используется ожидаемая учётная запись;
- последнее время запуска обновилось;
- задача не завершилась аварийно сразу после старта.

### 9. Проверить логи и наличие скрытого файла резервной копии

Проверьте:

- `C:\ProgramData\HLA_PostgresBackup\Logs\backup_watcher.log`
- `D:\HLA_Laboratory_System\hla_postgres_backup.dump`

Удобные команды проверки:

```powershell
Get-Content "C:\ProgramData\HLA_PostgresBackup\Logs\backup_watcher.log" -Tail 50
Get-Item -Force "D:\HLA_Laboratory_System\hla_postgres_backup.dump"
```

В логе должны появиться записи о:

- запуске скрипта-наблюдателя;
- первом резервном копировании на старте или при первом успешном подключении;
- последующих резервных копиях после изменений в PostgreSQL;
- плановых контрольных резервных копиях.

### 10. Выполнить функциональную проверку на боевом сценарии

- запустите приложение;
- выполните импорт, который гарантированно записывает данные в PostgreSQL;
- дождитесь завершения импорта;
- подождите не менее `DebounceSeconds` секунд
  (по умолчанию `60` секунд);
- проверьте, что у скрытого файла резервной копии обновилось время изменения;
- дополнительно проверьте новые записи в `backup_watcher.log`.

Для быстрой проверки времени изменения удобно выполнить:

```powershell
Get-Item -Force "D:\HLA_Laboratory_System\hla_postgres_backup.dump" | Select-Object FullName, LastWriteTime, Length
```

### Краткая рабочая команда для типовой установки

Если на боевом ПК используются именно стандартные параметры подключения:

- `localhost`
- `5432`
- `hla_db`
- `postgres`
- `0`

то последовательность обычно такая:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_AutoBackup.ps1" -ListOnly
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_AutoBackup.ps1" -RunOnce
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-PostgresBackupTask_AllUsers.ps1" -TaskUser "PCNAME\SomeUser"
Start-ScheduledTask -TaskName "HLA PostgreSQL Auto Backup"
```

### Эксплуатационные примечания для автоматического резервного копирования

- Скрипт защищён от запуска второго экземпляра на том же компьютере.
- Если PostgreSQL недоступен в момент старта, скрипт-наблюдатель не завершается,
  а остаётся в ожидании и начинает выполнять резервное копирование после восстановления связи.
- При каждом успешном резервном копировании предыдущий файл резервной копии заменяется новым.
- Скрытый файл создаётся в корне `D:\HLA_Laboratory_System`,
  поэтому он уезжает в сетевое зеркало вместе с основной локальной файловой
  базой.
- Если каталог `D:\HLA_Laboratory_System` изменится, нужно обязательно
  обновить параметр `-BackupFile`.
- Если изменятся хост, порт, логин, пароль или имя БД, нужно синхронно
  обновить параметры запуска скрипта резервного копирования.

### Когда обязательно нужно обновить настройки автоматического резервного копирования

Нужно пересмотреть параметры, если изменилось хотя бы одно из перечисленного ниже:

- имя БД PostgreSQL;
- хост или порт PostgreSQL;
- пользователь или пароль PostgreSQL;
- путь к каталогу `PostgreSQL\bin`;
- локальный корень файловой базы `D:\HLA_Laboratory_System`;
- путь размещения самих `.ps1` файлов;
- учётная запись, от имени которой запускается задача.

### Краткий чек-лист перед рабочим запуском автоматического резервного копирования

- `pg_dump.exe` и `psql.exe` доступны
- `-ListOnly` выполнен без ошибок
- `-RunOnce` создал реальную резервную копию
- файл `D:\HLA_Laboratory_System\hla_postgres_backup.dump` существует
- файл резервной копии скрыт и обновляет `LastWriteTime`
- задача `HLA PostgreSQL Auto Backup` зарегистрирована
- в `backup_watcher.log` есть записи о старте и резервном копировании
- тестовый импорт из приложения действительно приводит к обновлению файла резервной копии

## Копирование удалённой PostgreSQL в локальную PostgreSQL

Сценарий предназначен для автоматического обновления локальной базы данных
PostgreSQL из удалённой базы данных. Типовой вариант для проекта:
удалённая база `hla_db` копируется в локальную базу `hla_db_remote` один раз
в час.

Скрипт `HLA_Postgres_RemoteToLocal_Copy.ps1` выполняет следующие действия:

- создаёт резервный файл удалённой базы средствами `pg_dump`;
- восстанавливает этот файл во временную локальную базу средствами `pg_restore`;
- проверяет восстановленную временную базу;
- заменяет локальную целевую базу успешно восстановленной копией;
- записывает журнал в
  `C:\ProgramData\HLA_PostgresRemoteCopy\Logs\remote_copy.log`;
- повторяет обновление через `RefreshIntervalMinutes`
  (по умолчанию `60` минут);
- при ошибке выполняет следующую попытку через `RetryMinutes`
  (по умолчанию `15` минут).

### Важные предупреждения для копирования удалённой PostgreSQL

- Локальная целевая база будет заменяться содержимым удалённой базы.
  Не храните в ней данные, которых нет на удалённом сервере.
- При замене базы скрипт завершает активные подключения к локальной целевой
  базе. Не запускайте обновление во время работы пользователей с этой базой.
- Для локального подключения требуется пользователь PostgreSQL с правом
  создавать, переименовывать и удалять базы данных. Обычно для этой задачи
  используется локальный пользователь `postgres`.
- Для удалённого подключения требуется пользователь PostgreSQL с правом
  подключения к исходной базе и чтения её данных. Имя этого пользователя
  задаётся администратором конкретного сервера и передаётся через
  параметр `-RemoteDbUser`.
- Для служебных паролей рекомендуется использовать длинные пароли из
  латинских букв, цифр и специальных символов. Это снижает риск ошибок при
  работе старых консолей Windows и Планировщика заданий.
- Пароли, указанные в `-ScriptArguments`, доступны администраторам компьютера
  в свойствах задачи Планировщика. Если это недопустимо, задайте пароли через
  переменные окружения `HLA_REMOTE_DB_PASSWORD` и `HLA_APP_DB_PASSWORD`.
- На локальном компьютере должны быть доступны `pg_dump.exe`,
  `pg_restore.exe` и `psql.exe`. При стандартной установке PostgreSQL 18 они
  находятся в каталоге `C:\Program Files\PostgreSQL\18\bin`.

### Имена баз данных для копирования

По умолчанию основной скрипт копирует:

- удалённую базу `hla_db`;
- в локальную базу `hla_db_remote`.

Если в вашей установке используются другие имена, например нужно копировать
`hla_db_before`, задайте их явно:

- при ручном запуске основного скрипта параметрами `-RemoteDbName` и
  `-LocalDbName`;
- при регистрации задачи такими же параметрами установочного скрипта
  `Install-HLA-PostgresRemoteCopyTask_AllUsers.ps1`.

Не передавайте `-RemoteDbName` и `-LocalDbName` внутри `-ScriptArguments`
установочного скрипта: для них есть отдельные параметры установщика.

### Настройка удалённого PostgreSQL

На удалённом сервере создайте отдельного пользователя PostgreSQL для чтения
исходной базы. В примере ниже используется условное имя
`ИМЯ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ`; замените его на имя, принятое в вашей
инфраструктуре.

```sql
CREATE ROLE ИМЯ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ
    LOGIN
    PASSWORD 'СЛОЖНЫЙ_ПАРОЛЬ'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION;

GRANT CONNECT ON DATABASE ИМЯ_БАЗЫ_ИСТОЧНИКА TO ИМЯ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ;
GRANT pg_read_all_data TO ИМЯ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ;
```

Проверьте расположение конфигурационных файлов:

```sql
SHOW config_file;
SHOW hba_file;
```

В файле `postgresql.conf` разрешите подключение по сетевому интерфейсу:

```conf
listen_addresses = '*'
```

Вместо `*` можно указать конкретный IP-адрес сервера PostgreSQL.

В файле `pg_hba.conf` добавьте правило для IP-адреса локального компьютера,
на котором будет выполняться копирование. Для PostgreSQL 18 рекомендуется
использовать `scram-sha-256`:

```conf
host    ИМЯ_БАЗЫ_ИСТОЧНИКА    ИМЯ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ    IP_ВАШЕГО_ПК/32    scram-sha-256
```

Если сервер уже настроен на проверку паролей `md5`, допускается правило:

```conf
host    ИМЯ_БАЗЫ_ИСТОЧНИКА    ИМЯ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ    IP_ВАШЕГО_ПК/32    md5
```

После изменения `postgresql.conf` перезапустите службу PostgreSQL 18.
После изменения только `pg_hba.conf` достаточно перезагрузить конфигурацию
PostgreSQL; перезапуск службы также допустим.

Откройте во входящих правилах брандмауэра Windows TCP-порт PostgreSQL,
обычно `5432`.

### 1. Проверить доступность удалённого сервера

На локальном компьютере выполните:

```powershell
Test-NetConnection "АДРЕС_УДАЛЁННОГО_POSTGRESQL" -Port 5432
```

Если `TcpTestSucceeded` равно `False`, проверьте сетевую доступность,
`listen_addresses`, `pg_hba.conf` и правила брандмауэра на удалённом сервере.

### 2. Проверить служебные программы PostgreSQL

На локальном компьютере выполните:

```powershell
Get-Command pg_dump.exe
Get-Command pg_restore.exe
Get-Command psql.exe
```

Если команды не найдены, передавайте путь к каталогу `bin` через параметр
`-PgBinDir`, например:

```text
C:\Program Files\PostgreSQL\18\bin
```

### 3. Выполнить проверочный запуск

Проверочный запуск устанавливает соединения и проверяет параметры, но не
создаёт резервный файл и не изменяет локальную базу.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_RemoteToLocal_Copy.ps1" -ListOnly -RemoteDbHost "АДРЕС_УДАЛЁННОГО_POSTGRESQL" -RemoteDbUser "ИМЯ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ" -RemoteDbPassword "ПАРОЛЬ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ" -RemoteDbName "ИМЯ_УДАЛЁННОЙ_БАЗЫ" -LocalDbName "ИМЯ_ЛОКАЛЬНОЙ_КОПИИ" -LocalDbPassword "ЛОКАЛЬНЫЙ_ПАРОЛЬ_POSTGRES" -PgBinDir "C:\Program Files\PostgreSQL\18\bin"
```

Если пароль локального пользователя `postgres` равен `0`, параметр
`-LocalDbPassword` можно не указывать.

Проверьте журнал:

```powershell
Get-Content "C:\ProgramData\HLA_PostgresRemoteCopy\Logs\remote_copy.log" -Tail 50
```

Ожидаемый результат:

- найдены `pg_dump.exe`, `pg_restore.exe` и `psql.exe`;
- подключение к удалённой базе выполнено успешно;
- подключение к локальному PostgreSQL выполнено успешно;
- в журнале есть запись об успешном проверочном запуске без изменения
  локальной базы.

### 4. Выполнить первое копирование вручную

После успешного проверочного запуска выполните разовое копирование:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_RemoteToLocal_Copy.ps1" -RunOnce -RemoteDbHost "АДРЕС_УДАЛЁННОГО_POSTGRESQL" -RemoteDbUser "ИМЯ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ" -RemoteDbPassword "ПАРОЛЬ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ" -RemoteDbName "ИМЯ_УДАЛЁННОЙ_БАЗЫ" -LocalDbName "ИМЯ_ЛОКАЛЬНОЙ_КОПИИ" -LocalDbPassword "ЛОКАЛЬНЫЙ_ПАРОЛЬ_POSTGRES" -PgBinDir "C:\Program Files\PostgreSQL\18\bin"
```

Проверьте журнал:

```powershell
Get-Content "C:\ProgramData\HLA_PostgresRemoteCopy\Logs\remote_copy.log" -Tail 80
```

Ожидаемый результат:

- создан временный файл резервной копии удалённой базы;
- временная локальная база восстановлена;
- локальная `ИМЯ_ЛОКАЛЬНОЙ_КОПИИ` заменена новой копией;
- в журнале есть запись об успешном завершении копирования.

### 5. Зарегистрировать задачу Планировщика

В PowerShell от имени администратора определите пользователя, от которого
будет выполняться задача:

```powershell
$env:COMPUTERNAME
whoami
```

Зарегистрируйте задачу. Интервал `60` минут соответствует одному часу.

```powershell
& "C:\Program Files\HLA_Laboratory_System\_internal\Scripts\Install-HLA-PostgresRemoteCopyTask_AllUsers.ps1" -TaskUser "ИМЯ_ПК_ИЛИ_ДОМЕНА\ИмяПользователя" -RemoteDbHost "АДРЕС_УДАЛЁННОГО_POSTGRESQL" -RemoteDbName "ИМЯ_УДАЛЁННОЙ_БАЗЫ" -RemoteDbUser "ИМЯ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ" -LocalDbName "ИМЯ_ЛОКАЛЬНОЙ_КОПИИ" -RefreshIntervalMinutes 60 -ScriptArguments '-RemoteDbPassword "ПАРОЛЬ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ" -LocalDbPassword "ЛОКАЛЬНЫЙ_ПАРОЛЬ_POSTGRES" -PgBinDir "C:\Program Files\PostgreSQL\18\bin"'
```

Если задача уже была зарегистрирована с другим интервалом, повторный запуск
установочного скрипта перерегистрирует её с новым значением.

Если пароль локального пользователя `postgres` равен `0`, параметр
`-LocalDbPassword "ЛОКАЛЬНЫЙ_ПАРОЛЬ_POSTGRES"` можно убрать.

Установочные скрипты рекомендуется вызывать через оператор `&` из текущей
сессии PowerShell. Это обеспечивает корректную передачу строки
`-ScriptArguments` с кавычками и путями вида `C:\Program Files\...`.

### Готовый набор команд через `whoami`

Перед запуском блока ниже обязательно проверьте и при необходимости замените:

- `$RemoteHost = 'АДРЕС_УДАЛЁННОГО_POSTGRESQL'`
  на адрес удалённого PostgreSQL;
- `$RemoteDbName = 'ИМЯ_УДАЛЁННОЙ_БАЗЫ'`
  на имя удалённой базы PostgreSQL, если оно отличается;
- `$RemoteUser = 'ИМЯ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ'`
  на имя пользователя удалённого PostgreSQL;
- `$RemotePassword = 'ПАРОЛЬ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ'`
  на пароль пользователя удалённого PostgreSQL;
- `$LocalDbName = 'ИМЯ_ЛОКАЛЬНОЙ_КОПИИ'`
  на имя локальной базы PostgreSQL, если оно отличается;
- `$LocalPassword = '0'`
  на пароль локального пользователя `postgres`, если он отличается;
- если задача должна работать не от текущего пользователя,
  замените строку `$TaskUser = (whoami).Trim()` на нужную учётную запись.

Команды:

```powershell
$ScriptDir = 'C:\Program Files\HLA_Laboratory_System\_internal\Scripts'
$PgBin = 'C:\Program Files\PostgreSQL\18\bin'
$RemoteHost = 'АДРЕС_УДАЛЁННОГО_POSTGRESQL'
$RemoteDbName = 'ИМЯ_УДАЛЁННОЙ_БАЗЫ'
$RemoteUser = 'ИМЯ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ'
$RemotePassword = 'ПАРОЛЬ_УДАЛЁННОГО_ПОЛЬЗОВАТЕЛЯ'
$LocalDbName = 'ИМЯ_ЛОКАЛЬНОЙ_КОПИИ'
$LocalPassword = '0'
$TaskUser = (whoami).Trim()
$ScriptArguments = '-RemoteDbPassword "' + $RemotePassword + '" -LocalDbPassword "' + $LocalPassword + '" -PgBinDir "' + $PgBin + '"'

$machinePath = [Environment]::GetEnvironmentVariable('Path','Machine')
if (($machinePath -split ';') -notcontains $PgBin) {
    [Environment]::SetEnvironmentVariable('Path', ($machinePath.TrimEnd(';') + ';' + $PgBin), 'Machine')
}
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')

Get-Command pg_dump.exe
Get-Command pg_restore.exe
Get-Command psql.exe

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ScriptDir\HLA_Postgres_RemoteToLocal_Copy.ps1" -ListOnly -RemoteDbHost $RemoteHost -RemoteDbName $RemoteDbName -RemoteDbUser $RemoteUser -RemoteDbPassword $RemotePassword -LocalDbName $LocalDbName -LocalDbPassword $LocalPassword -PgBinDir $PgBin
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ScriptDir\HLA_Postgres_RemoteToLocal_Copy.ps1" -RunOnce -RemoteDbHost $RemoteHost -RemoteDbName $RemoteDbName -RemoteDbUser $RemoteUser -RemoteDbPassword $RemotePassword -LocalDbName $LocalDbName -LocalDbPassword $LocalPassword -PgBinDir $PgBin

Get-Content 'C:\ProgramData\HLA_PostgresRemoteCopy\Logs\remote_copy.log' -Tail 80

& "$ScriptDir\Install-HLA-PostgresRemoteCopyTask_AllUsers.ps1" -TaskUser $TaskUser -RemoteDbHost $RemoteHost -RemoteDbName $RemoteDbName -RemoteDbUser $RemoteUser -LocalDbName $LocalDbName -ScriptArguments $ScriptArguments

Start-ScheduledTask -TaskName 'HLA PostgreSQL Remote To Local Copy'
Get-ScheduledTask -TaskName 'HLA PostgreSQL Remote To Local Copy'
Get-ScheduledTaskInfo -TaskName 'HLA PostgreSQL Remote To Local Copy'
```

### 6. Запустить задачу вручную сразу после регистрации

```powershell
Start-ScheduledTask -TaskName "HLA PostgreSQL Remote To Local Copy"
```

### 7. Проверить, что задача реально создана и стартовала

```powershell
Get-ScheduledTask -TaskName "HLA PostgreSQL Remote To Local Copy"
Get-ScheduledTaskInfo -TaskName "HLA PostgreSQL Remote To Local Copy"
```

Нужно убедиться, что:

- задача зарегистрирована без ошибок;
- используется ожидаемая учётная запись;
- последнее время запуска обновилось;
- задача не завершилась аварийно сразу после старта.

### 8. Проверить журнал работы

Проверьте:

- `C:\ProgramData\HLA_PostgresRemoteCopy\Logs\remote_copy.log`

Удобная команда проверки:

```powershell
Get-Content "C:\ProgramData\HLA_PostgresRemoteCopy\Logs\remote_copy.log" -Tail 80
```

В журнале должны появиться записи о запуске скрипта, проверке подключений и
успешном копировании удалённой базы в локальную базу.

### 9. Выполнить функциональную проверку

- подключитесь к локальной базе `ИМЯ_ЛОКАЛЬНОЙ_КОПИИ` через pgAdmin 4 или `psql`;
- проверьте, что в локальной базе появились ожидаемые таблицы и данные;
- при необходимости запустите приложение с параметрами подключения к локальной
  `ИМЯ_ЛОКАЛЬНОЙ_КОПИИ`;
- проверьте сценарий, для которого нужна локальная копия удалённой базы.

Пример простой проверки через `psql`:

```powershell
psql.exe -h localhost -p 5432 -U postgres -d ИМЯ_ЛОКАЛЬНОЙ_КОПИИ -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema');"
```

### Эксплуатационные примечания для копирования удалённой PostgreSQL

- Скрипт защищён от запуска второго экземпляра на том же компьютере.
- При ошибке подключения или восстановления локальная целевая база не
  заменяется неудачной копией.
- Временные файлы резервной копии удаляются после успешного или неуспешного
  прохода, если не указан параметр `-KeepDump`.
- Временная локальная база удаляется после ошибки восстановления или публикации,
  если её удалось безопасно удалить.
- Если удалённая база недоступна, следующая попытка выполняется через
  `RetryMinutes`, по умолчанию через `15` минут.
- Если копирование прошло успешно, следующий плановый проход выполняется через
  `RefreshIntervalMinutes`, по умолчанию через `60` минут.

### Когда обязательно нужно обновить настройки копирования удалённой PostgreSQL

Нужно пересмотреть параметры, если изменилось хотя бы одно из перечисленного ниже:

- адрес, порт или имя удалённой базы PostgreSQL;
- имя пользователя или пароль удалённого PostgreSQL;
- адрес, порт или имя локальной базы PostgreSQL;
- имя пользователя или пароль локального PostgreSQL;
- путь к каталогу `PostgreSQL\bin`;
- путь размещения самих `.ps1` файлов;
- учётная запись, от имени которой запускается задача;
- требуемый интервал обновления локальной базы.

### Краткий чек-лист перед рабочим запуском копирования удалённой PostgreSQL

- на удалённом PostgreSQL создан пользователь для чтения исходной базы;
- пользователю выдано право `CONNECT` на базу `ИМЯ_УДАЛЁННОЙ_БАЗЫ`;
- пользователю выдана предопределённая роль `pg_read_all_data`;
- удалённый PostgreSQL принимает сетевые подключения;
- в `pg_hba.conf` разрешён IP-адрес локального компьютера;
- брандмауэр удалённого компьютера пропускает TCP-порт `5432`;
- на локальном компьютере доступны `pg_dump.exe`, `pg_restore.exe` и `psql.exe`;
- проверочный запуск `-ListOnly` выполнен без ошибок;
- разовый запуск `-RunOnce` успешно обновил локальную `ИМЯ_ЛОКАЛЬНОЙ_КОПИИ`;
- задача `HLA PostgreSQL Remote To Local Copy` зарегистрирована;
- в `remote_copy.log` есть запись об успешном копировании.
