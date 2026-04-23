# Обновление программы HLA на рабочих ПК

Этот каталог содержит эксплуатационный сценарий для автоматического обновления
установленной программы HLA на рабочих компьютерах локальной сети.

Сценарий использует один компьютер как источник актуальной версии программы и
копирует его папку установки на остальные компьютеры.

В этой инструкции используются условные имена:

- `ИМЯ_ПК_ИСТОЧНИКА` - компьютер, на котором лежит актуальная версия программы;
- `ИМЯ_ПК_ДЛЯ_ПРОВЕРКИ` - любой один целевой ПК, на котором удобно проверить доступ;
- `ИМЯ_ПК_ИЛИ_ДОМЕНА\ИмяПользователя` - учётная запись для задачи Планировщика.

#### Назначение

- `HLA_ProgramFiles_RemoteUpdate.ps1`:
  основной скрипт обновления. Умеет выполнять тестовый прогон `-ListOnly`,
  разовое обновление `-RunOnce` и постоянный watcher-режим.
- `Install-HLA-ProgramFilesRemoteUpdateTask_AllUsers.ps1`:
  регистрирует задачу Планировщика Windows, которая запускает основной скрипт
  автоматически при старте компьютера-источника.

#### Значения и пути

- Папка-источник на компьютере-источнике:
  `C:\Program Files\HLA_Laboratory_System\HLA_Laboratory_System_Update`
- Папка назначения на каждом целевом ПК:
  `C:\Program Files\HLA_Laboratory_System`
- Каталог скриптов:
  `C:\Program Files\HLA_Laboratory_System\Scripts\Update`
- Путь к основному скрипту для задачи:
  `C:\Program Files\HLA_Laboratory_System\Scripts\Update\HLA_ProgramFiles_RemoteUpdate.ps1`
- Имя задачи Планировщика:
  `HLA Program Files Remote Update`
- Служебные логи:
  `C:\ProgramData\HLA_Laboratory_System\HLA_ProgramFilesRemoteUpdate\Logs`
- Служебное состояние:
  `C:\ProgramData\HLA_Laboratory_System\HLA_ProgramFilesRemoteUpdate\program_update_state.json`
- Пауза после изменений источника:
  `60` секунд
- Повтор для недоступных ПК:
  `60` минут
- Контрольная проверка источника:
  `1440` минут, то есть 24 часа

Компьютер-источник и список целевых ПК задаются параметрами основного скрипта:

- `-SourceComputer`
- `-TargetComputers`

Если эти параметры не передавать, будут использованы значения, прописанные в
самом `HLA_ProgramFiles_RemoteUpdate.ps1`. Если имена компьютеров в сети
изменились, нужно обновить эти параметры при запуске или в самом скрипте.

#### Как работает сценарий

Основной скрипт работает через SMB-пути административной шары `C$`, например:

`\\ИМЯ_ПК_ИСТОЧНИКА\C$\Program Files\HLA_Laboratory_System\HLA_Laboratory_System_Update`

`\\ИМЯ_ПК_ДЛЯ_ПРОВЕРКИ\C$\Program Files\HLA_Laboratory_System`

PowerShell Remoting не требуется.

Для каждого целевого ПК выполняются две фазы:

- `clear`: папка назначения зеркалируется с временной пустой папкой;
- `copy`: папка назначения зеркалируется с актуальной папкой на `ИМЯ_ПК_ИСТОЧНИКА`.

Если целевой ПК выключен или недоступен, остальные ПК всё равно обновляются.
Недоступный ПК остаётся в pending-списке, и скрипт повторяет обновление для
него раз в `RetryFailedMinutes`.

Watcher сохраняет pending-список и SHA256-подпись содержимого источника в
служебный state-файл. После перезагрузки компьютера с задачей Планировщика
скрипт не перезаписывает все ПК заново, если источник не изменился. В этом
случае он запускает watcher и повторяет обновление только для pending-ПК.

#### Общая философия настройки

- Сначала выполняется `-ListOnly`.
- Затем выполняется `-RunOnce`.
- Только после этого регистрируется задача Планировщика.
- После регистрации задача запускается вручную для проверки.
- Затем проверяются логи.

Для рабочей эксплуатации рекомендуется не менять команды вручную в разных
местах, а в начале готового блока задать переменные `$SourceComputer` и
`$TaskUser`.

#### Важные предупреждения

- Скрипт использует `robocopy /MIR`.
  Это зеркалирование, а не обычное копирование.
- Всё, что есть в папке назначения, но отсутствует в источнике на
  `ИМЯ_ПК_ИСТОЧНИКА`, будет удалено.
- Не храните вручную важные отдельные файлы в папке назначения на рабочих ПК,
  если этих файлов нет в эталонной папке на `ИМЯ_ПК_ИСТОЧНИКА`.
- Перед реальным обновлением скрипт проверяет, что источник существует и не
  пустой. Пустой источник запрещён без явного `-AllowEmptySource`.
- Если связь с ПК оборвалась после фазы `clear`, но до завершения `copy`,
  папка на этом ПК может временно остаться пустой или частично обновлённой.
  Следующая успешная попытка обновит её заново.
- На старых сборках Windows `robocopy` может не поддерживать часть расширенных
  ключей, включая `/IORATE`, `/THRESHOLD`, `/COMPRESS`, `/MT`, `/DCOPY`,
  `/XJ`, `/IT` или `/Z`. Скрипт пропускает неподдерживаемые опции и при
  необходимости повторяет запуск в минимальном совместимом режиме.

## Автоматическое обновление программы

Ниже приведён рекомендуемый порядок внедрения.

#### Предварительные условия

- Команды выполняются на `ИМЯ_ПК_ИСТОЧНИКА`.
- PowerShell открыт от имени администратора.
- Скрипты лежат в:
  `C:\Program Files\HLA_Laboratory_System\Scripts\Update`
- У выбранного пользователя есть доступ на чтение к папке источника.
- У выбранного пользователя есть доступ на запись к `C$` на целевых ПК.
- На целевых ПК существует или может быть создана папка:
  `C:\Program Files\HLA_Laboratory_System`

##### 1. Определить пользователя для Планировщика

В той же административной сессии выполнить:

```powershell
$env:COMPUTERNAME
whoami
```

Для параметра `-TaskUser` используйте полный логон пользователя, например:

- `PCNAME\SomeUser`
- `DOMAIN\hla_sync`

Рекомендуется использовать постоянную учётную запись, у которой гарантирован
доступ к источнику и к административным шарам целевых ПК.

##### 2. Проверить доступ к источнику и одному целевому ПК

Перед запуском замените `ИМЯ_ПК_ИСТОЧНИКА` и `ИМЯ_ПК_ДЛЯ_ПРОВЕРКИ` на реальные имена:

```powershell
$SourceComputer = 'ИМЯ_ПК_ИСТОЧНИКА'
$ProbeTargetComputer = 'ИМЯ_ПК_ДЛЯ_ПРОВЕРКИ'

Test-Path "C:\Program Files\HLA_Laboratory_System"
Test-Path "\\$SourceComputer\C$\Program Files\HLA_Laboratory_System\HLA_Laboratory_System_Update"
Test-Path "\\$ProbeTargetComputer\C$\Program Files\HLA_Laboratory_System"
```

Ожидаемый результат:

- локальная папка источника доступна;
- административная шара `C$` источника доступна;
- административная шара `C$` хотя бы одного целевого ПК доступна.

##### 3. Проверить тестовый запуск без копирования

Выполнить:

```powershell
$ScriptDir = 'C:\Program Files\HLA_Laboratory_System\Scripts\Update'
$SourceComputer = 'ИМЯ_ПК_ИСТОЧНИКА'

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ScriptDir\HLA_ProgramFiles_RemoteUpdate.ps1" -ListOnly -SourceComputer $SourceComputer
```

После этого проверьте:

```powershell
Get-Content "C:\ProgramData\HLA_Laboratory_System\HLA_ProgramFilesRemoteUpdate\Logs\program_update.log" -Tail 120
```

Ожидаемый результат:

- скрипт отработал без критических ошибок;
- реальные удаления и копирования не выполнялись;
- в логах видно, какие ПК доступны, а какие нет.

##### 4. Выполнить первое реальное обновление вручную

После успешного `-ListOnly` выполнить:

```powershell
$ScriptDir = 'C:\Program Files\HLA_Laboratory_System\Scripts\Update'
$SourceComputer = 'ИМЯ_ПК_ИСТОЧНИКА'

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ScriptDir\HLA_ProgramFiles_RemoteUpdate.ps1" -RunOnce -SourceComputer $SourceComputer
```

После этого проверьте:

```powershell
Get-Content "C:\ProgramData\HLA_Laboratory_System\HLA_ProgramFilesRemoteUpdate\Logs\program_update.log" -Tail 120
```

Ожидаемый результат:

- доступные ПК обновлены;
- недоступные ПК перечислены как failed targets;
- если failed targets нет, ручное обновление всех ПК завершилось успешно.

##### 5. Зарегистрировать задачу Планировщика

При запуске installer-скрипта из PowerShell рекомендуется использовать `&`,
чтобы параметры и кавычки передавались без искажений.

Выполнить, заменив значения на реальные:

```powershell
$ScriptDir = 'C:\Program Files\HLA_Laboratory_System\Scripts\Update'
$SourceComputer = 'ИМЯ_ПК_ИСТОЧНИКА'
$TaskUser = 'ИМЯ_ПК_ИЛИ_ДОМЕНА\ИмяПользователя'

& "$ScriptDir\Install-HLA-ProgramFilesRemoteUpdateTask_AllUsers.ps1" -TaskUser $TaskUser -SourceComputer $SourceComputer
```

Установщик запросит пароль указанного пользователя и создаст задачу:

`HLA Program Files Remote Update`

Если нужно передать дополнительные параметры основному скрипту, используйте
`-ScriptArguments`, например:

```powershell
& "$ScriptDir\Install-HLA-ProgramFilesRemoteUpdateTask_AllUsers.ps1" -TaskUser $TaskUser -SourceComputer $SourceComputer -ScriptArguments '-RetryFailedMinutes 30'
```

Не передавайте `-RunOnce` и `-ListOnly` через `-ScriptArguments`. Эти режимы
предназначены только для ручной проверки основного скрипта.

### Готовый набор команд через whoami

Команды ниже вставляйте в PowerShell одним блоком именно в указанном порядке.
PowerShell выполняет строки сверху вниз, поэтому переменные должны быть заданы
до проверки логов, установки задачи и запуска задачи.

Перед запуском блока ниже обязательно замените:

- `$SourceComputer = 'ИМЯ_ПК_ИСТОЧНИКА'`

Если задача должна работать не от текущего пользователя, замените строку
`$TaskUser = (whoami).Trim()` на нужную учётную запись.

```powershell
$ScriptDir = 'C:\Program Files\HLA_Laboratory_System\Scripts\Update'
$SourceComputer = 'ИМЯ_ПК_ИСТОЧНИКА'
$TaskUser = (whoami).Trim()
$TaskName = 'HLA Program Files Remote Update'
$LogFile = 'C:\ProgramData\HLA_Laboratory_System\HLA_ProgramFilesRemoteUpdate\Logs\program_update.log'

$env:COMPUTERNAME
$TaskUser

if (-not (Test-Path "$ScriptDir\Install-HLA-ProgramFilesRemoteUpdateTask_AllUsers.ps1")) {
    throw "Installer script was not found: $ScriptDir\Install-HLA-ProgramFilesRemoteUpdateTask_AllUsers.ps1"
}

if (-not (Test-Path "$ScriptDir\HLA_ProgramFiles_RemoteUpdate.ps1")) {
    throw "Main script was not found: $ScriptDir\HLA_ProgramFiles_RemoteUpdate.ps1"
}

& "$ScriptDir\Install-HLA-ProgramFilesRemoteUpdateTask_AllUsers.ps1" -TaskUser $TaskUser -SourceComputer $SourceComputer

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5

Get-ScheduledTask -TaskName $TaskName
Get-ScheduledTaskInfo -TaskName $TaskName

if (Test-Path $LogFile) {
    Get-Content $LogFile -Tail 120
}
else {
    Write-Host "Log file was not created yet: $LogFile"
}
```

##### 6. Запустить задачу вручную после регистрации

Выполнить:

```powershell
Start-ScheduledTask -TaskName "HLA Program Files Remote Update"
```

##### 7. Проверить, что задача создана и реально стартовала

Выполнить:

```powershell
Get-ScheduledTask -TaskName "HLA Program Files Remote Update"
Get-ScheduledTaskInfo -TaskName "HLA Program Files Remote Update"
```

Нужно убедиться, что:

- задача зарегистрирована без ошибок;
- используется ожидаемая учётная запись;
- задача находится в рабочем состоянии после запуска.

##### 8. Проверить логи работы

Проверить файлы:

- `C:\ProgramData\HLA_Laboratory_System\HLA_ProgramFilesRemoteUpdate\Logs\program_update.log`
- `C:\ProgramData\HLA_Laboratory_System\HLA_ProgramFilesRemoteUpdate\Logs\clear_ИМЯПК_YYYYMMDD.log`
- `C:\ProgramData\HLA_Laboratory_System\HLA_ProgramFilesRemoteUpdate\Logs\copy_ИМЯПК_YYYYMMDD.log`

Команды:

```powershell
Get-Content "C:\ProgramData\HLA_Laboratory_System\HLA_ProgramFilesRemoteUpdate\Logs\program_update.log" -Tail 120
Select-String -Path "C:\ProgramData\HLA_Laboratory_System\HLA_ProgramFilesRemoteUpdate\Logs\program_update.log" -Pattern "ERROR"
Get-ChildItem "C:\ProgramData\HLA_Laboratory_System\HLA_ProgramFilesRemoteUpdate\Logs" -Filter "*ИМЯ_ПК*.log"
```

##### 9. Выполнить функциональную проверку

- обновить содержимое папки на `ИМЯ_ПК_ИСТОЧНИКА`;
- подождать не менее `DebounceSeconds`, по умолчанию `60` секунд;
- проверить, что доступные ПК получили новую версию;
- включить ранее недоступный ПК и убедиться, что он обновился после очередной
  повторной попытки.

#### Эксплуатационные примечания

- Без `-ListOnly` и `-RunOnce` основной скрипт работает как watcher.
- Watcher при старте читает сохранённое состояние.
- Если источник не изменился, watcher не перезаписывает уже обновлённые ПК.
- Если источника в state-файле ещё нет, изменилась подпись источника или
  изменились ключевые настройки, watcher выполняет полный прогон один раз.
- После изменений источника watcher пересчитывает подпись и обновляет все ПК
  только если содержимое источника действительно изменилось.
- Failed/pending ПК повторяются раз в `RetryFailedMinutes`.
- Раз в `FullResyncMinutes` выполняется контрольная проверка подписи источника.
  Если подпись не изменилась, `clear` и `copy` для всех ПК не запускаются.
- Скрипт защищён от второго экземпляра на том же компьютере.
- Задача Планировщика создаётся одна на весь компьютер.

#### Когда обязательно нужно обновить настройки

Нужно пересмотреть параметры задачи, если изменилось хотя бы одно:

- компьютер-источник;
- путь к папке-источнику;
- путь назначения на рабочих ПК;
- список рабочих ПК;
- путь размещения `.ps1` файлов;
- учётная запись задачи;
- права доступа к `C$` на рабочих ПК.

В таких случаях проверьте:

- `-SourceComputer`
- `-SourceLocalPath`
- `-DestinationLocalPath`
- `-TargetComputers`
- `-SyncScriptPath`
- `-ScriptArguments`

#### Краткий чек-лист перед рабочим запуском

- скрипты доступны в `C:\Program Files\HLA_Laboratory_System\Scripts\Update`
- источник на `ИМЯ_ПК_ИСТОЧНИКА` существует и не пустой
- административная шара `\\ИМЯ_ПК_ИСТОЧНИКА\C$` доступна
- хотя бы один целевой ПК доступен через `\\ИМЯ_ПК\C$`
- `-ListOnly` выполнен без неожиданных ошибок
- `-RunOnce` обновил доступные ПК
- задача зарегистрирована от правильного пользователя
- после ручного запуска задачи появились записи в `program_update.log`
- недоступные ПК попали в pending и повторяются по расписанию
