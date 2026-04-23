# Защита файловой базы HLA

Эта инструкция описывает единственную поддерживаемую схему `windows-file-base-v4` и предназначена только для чистой установки на локальный фиксированный NTFS-корень без существующей `.hla_internal`.

Профиль развёртывания всегда выбирается явно и должен точно совпадать с `FILE_BASE_DEPLOYMENT_MODE`, заданным на этапе сборки:

- `domain_network` — существующая доменная топология с каноническими UNC, SMB/Kerberos, авторитетным корнем и отдельным зеркалом;
- `local_single_host` — один авторитетный локальный корень на одном ПК; UNC, общий ресурс SMB файловой базы, зеркало и удалённый Writer запрещены.

Доступность домена или сети не переключает профиль автоматически. Несовпадение профиля сборки, манифеста, конфигурации Writer или профиля задачи приводит к безопасному отказу.

## Топология

### `domain_network`

- `D:\HLA_Laboratory_System` на сервере хранения — единственный физический авторитетный корень.
- `\\HLA-FB\HLA_Laboratory_System` — его постоянное SMB-имя для всех равноправных сборок `ENABLE_LABORATORY_ACTIONS=True`, включая приложение на самом сервере хранения.
- `\\HLA-VIEW\HLA_Laboratory_System` — отдельный односторонний корень роли `mirror` только для `ENABLE_LABORATORY_ACTIONS=False`.
- Обратной синхронизации из зеркала нет. Авторитетный корень и зеркало получают разные `root_uuid`; манифест зеркала содержит `mirror_of_root_uuid` авторитетного корня.

### `local_single_host`

- используется ровно один целевой путь `D:\HLA_Laboratory_System` на локальном фиксированном томе NTFS, но не корень тома;
- Laboratory и локальный Viewer читают один манифест с ролью `authoritative`;
- подключённые диски, `SUBST`, пути устройств, UNC и пути с точками повторной обработки, а также `root_role=mirror` отклоняются;
- ACL работает напрямую через локальный токен пакетного входа служебной записи;
- EFS использует один Writer через `\\.\pipe`; служба не зависит от `LanmanServer`, а удалённые клиенты канала отклоняются;
- сетевое зеркало этого корня не является частью профиля. Если файловой базой должны пользоваться программы на другом узле, выбирайте `domain_network`.

Оба режима используют одну структуру:

```text
<root>\
    <ОРГАН>\
        .source_files\    # постоянные исходные CSV
    .hla_internal\
        protection.json
        locks\writer.lock
        transactions\
        access_probe      # только EFS
        efs_recipients\   # только EFS
```

Только точная `.hla_internal` непосредственно в корне является служебной. Вложенные пользовательские объекты с таким именем остаются обычными данными. `transactions` содержит единственный долговечный журнал файловых операций и рабочие каталоги отката; они не размещаются в `%TEMP%` и не создают соседние `.bak__*` в пользовательском дереве.

Постоянный каталог исходных CSV имеет точное имя `.source_files` и находится непосредственно внутри каталога органа. Для уже существующих каталогов администратор обязан установить атрибут `Hidden` при подготовке чистого корня; каталоги, штатно создаваемые приложением, получают этот атрибут автоматически. Таблица PostgreSQL сохраняет имя `source_files`. Одноимённый каталог внутри рабочего каталога `transactions` остаётся внутренней категорией снимка операции и не относится к структуре органа.

Строгий манифест схемы 4 фиксирует `deployment_mode`, `root_uuid`, роль и `mirror_of_root_uuid`, режим защиты, физическую идентичность корня, `service_sid`, `reader_sids`, `writer_sids`, а для EFS также `service_certificate_sha1`, имя `access_probe`, его `access_probe_sha256` и точный набор `efs_recipients`. Неизвестные, отсутствующие или лишние поля отклоняются.

В командах ниже значения, зависящие от площадки, записаны в кавычках как русскоязычные подстановочные значения в угловых скобках, например `'<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'`. Перед запуском замените каждое такое значение: компактная проверка в блоке прерывает выполнение, если оно осталось. Фиксированные имена продукта, пути установки и канонические алиасы `HLA-FB`/`HLA-VIEW` подстановочными значениями не являются и не заменяются.

## Два режима защиты

Для клинических операций приложения, которые согласованно изменяют PostgreSQL и файловую базу, порядок допуска неизменен: сначала блокировка PostgreSQL уровня приложения для текущего клинического домена блокировок, затем эксклюзивный физический дескриптор `.hla_internal\locks\writer.lock`, проверка долговечного журнала и только после неё сама операция. При завершении сначала освобождается физическая блокировка, затем блокировка PostgreSQL. Зеркалирование и резервное копирование используют отдельные описанные ниже контуры, но также не обходят `writer.lock` и барьер журнала своего корня.

### ACL

`ENABLE_FILE_BASE_ENCRYPTION=False` использует постоянные NTFS ACL. В `domain_network` все Laboratory-приложения обращаются к каноническому UNC и выполняют запись непосредственно по SMB под одним `FILE_BASE_SERVICE_USER`. В `local_single_host` Laboratory-приложение работает с тем же корнем роли `authoritative` по локальному пути под фактической служебной учётной записью.

Инициализатор строит канонические ACL из политики манифеста, а не копирует текущую DACL корня: служебная запись, `SYSTEM` и `Administrators` получают служебные права, а `reader_sids` — только постоянное чтение. Имеющиеся разрешения корня в итоговую политику не переносятся.

Отдельная служба Writer в ACL-режиме не устанавливается. В `domain_network` Viewer читает зеркало по постоянной ACL только для чтения. В `local_single_host` Viewer читает тот же корень роли `authoritative` на единственном ПК, не создаёт зеркало и не получает права лабораторной записи.

### EFS

`ENABLE_FILE_BASE_ENCRYPTION=True` использует одновременно:

- постоянную NTFS ACL чтения для явно настроенных `reader_sids`;
- EFS-получателя служебной учётной записи;
- индивидуальный EFS-сертификат каждого разрешённого оператора;
- отдельную группу `writer_sids` только на авторитетном корне.

В `domain_network` Laboratory-приложения читают файлы по SMB под учётной записью оператора, а изменения передают по аутентифицированному протоколу именованных каналов службе **HLA File Base Writer** на сервере физического корня. В `local_single_host` Laboratory-приложение читает тот же локальный корень роли `authoritative` и обращается к единственному Writer через локальный `\\.\pipe`. Writer выполняет NTFS/EFS-операцию локально под фактической служебной учётной записью. Пароль и закрытый EFS-ключ этой записи не передаются клиенту. Writer принимает только конфигурацию схемы 2 и протокол версии 3, привязан к одному `root_uuid` и сверяет профиль в HELLO; в `local_single_host` удалённые клиенты блокируются двумя независимыми проверками: флагом `PIPE_REJECT_REMOTE_CLIENTS` и повторной серверной проверкой допуска до открытия сессии.

Перед фиксацией публикации каждого файла пользовательских данных вне служебной корневой `.hla_internal` Writer проверяет точный служебный сертификат и всех операторских получателей из манифеста; в `local_single_host` дополнительно требуется хотя бы один фактически назначенный системный DRA, а в `domain_network` DRA остаётся внешней политикой GPO и не входит в точную проверку получателей Writer. Публикация без успешной итоговой проверки требуемого состояния не считается успешной.

Только в `domain_network` на `HLA-VIEW` тот же Writer устанавливается в роли `mirror`. Он принимает только локальную административную команду публикации зеркала и не принимает обычные Laboratory-операции. Viewer-клиенты читают зеркало напрямую; Writer на их ПК не устанавливается. В `local_single_host` локальный Viewer читает корень роли `authoritative` напрямую и не устанавливает отдельный Writer.

EFS Writer не является резервным сервером хранения. В `domain_network`, если сервер физического корня недоступен, запись в этот корень недоступна, а Viewer может продолжить чтение уже опубликованного зеркала. В `local_single_host` отдельного резервного корня нет: отказ единственного корня роли `authoritative` прекращает и Laboratory-доступ, и локальное Viewer-чтение.

Итоговая матрица применения:

| Профиль/сборка | Защита | Корень | Writer |
|---|---|---|---|
| `domain_network`, `ENABLE_LABORATORY_ACTIONS=True` | `ENABLE_FILE_BASE_ENCRYPTION=False` | `\\HLA-FB\HLA_Laboratory_System` | не устанавливается |
| `domain_network`, `ENABLE_LABORATORY_ACTIONS=False` | `ENABLE_FILE_BASE_ENCRYPTION=False` | `\\HLA-VIEW\HLA_Laboratory_System` | не устанавливается |
| `domain_network`, `ENABLE_LABORATORY_ACTIONS=True` | `ENABLE_FILE_BASE_ENCRYPTION=True` | `\\HLA-FB\HLA_Laboratory_System` | роль `authoritative` только на сервере `HLA-FB` |
| `domain_network`, `ENABLE_LABORATORY_ACTIONS=False` | `ENABLE_FILE_BASE_ENCRYPTION=True` | `\\HLA-VIEW\HLA_Laboratory_System` | роль `mirror` только на сервере `HLA-VIEW`; на Viewer-ПК не устанавливается |
| `local_single_host`, Laboratory или локальный Viewer | ACL | один локальный NTFS-корень роли `authoritative` | не устанавливается |
| `local_single_host`, Laboratory или локальный Viewer | EFS | тот же локальный NTFS-корень роли `authoritative` | один локальный Writer роли `authoritative` на корне, если Laboratory выполняет изменения; Viewer не устанавливает отдельный Writer |

## Учётные записи и сертификаты

Для одного физического корня внутри клиники во всех сборках используется один логический `FILE_BASE_SERVICE_USER` формата `DOMAIN\User` и один SID. На ПК, присоединённом точно к указанному домену, разрешается только доменная запись. На ПК в рабочей группе или без присоединения к домену профиль `local_single_host` разрешает включённого локального `COMPUTER\User` с тем же именем пользователя после прямой и обратной проверки типа SID `SidTypeUser`. Другой домен, неизвестное состояние присоединения к домену, ошибка API, ошибка входа или несовпадение SID завершаются отказом без повторной попытки под другой учётной записью. Этот контракт задаётся на этапе сборки и не переопределяется пользователем во время работы приложения.

Настройте отдельные сущности:

- `ServiceUser` — логическая выделенная служебная учётная запись; в `domain_network` доменная, в профиле `local_single_host` на ПК в рабочей группе или без присоединения к домену — заранее созданный включённый локальный пользователь с тем же именем; не `SYSTEM`, не `Administrators` и не обычный оператор;
- `ReadAccount` — одна или несколько постоянных фактических групп чтения: доменных на ПК, присоединённом к точному настроенному домену, либо локальных `COMPUTER\Group` на ПК в рабочей группе или без присоединения к домену; `Everyone` и `Anonymous` намеренно не допускаются;
- `WriterAccount` — фактическая доменная или локальная группа пользователей Laboratory по тому же правилу топологии, которым разрешено инициировать EFS-запись в авторитетный корень;
- `OperatorCertificate` — для каждого EFS-оператора точное сопоставление фактического имени с сертификатом в формате `'<DOMAIN_OR_COMPUTER\УЧЁТНАЯ_ЗАПИСЬ_ОПЕРАТОРА>=<ПОЛНЫЙ_ПУТЬ_К_СЕРТИФИКАТУ.cer>'`.

`reader_sids` и EFS-получатели решают разные задачи и не обязаны совпадать. Пользователь EFS должен одновременно иметь право чтения NTFS и закрытый ключ соответствующего EFS-сертификата. На зеркале `writer_sids` всегда пуст: его изменяет только локальный Writer в режиме обслуживания.

Перед EFS-развёртыванием:

1. Выпустите служебной учётной записи один EFS-сертификат с закрытым ключом в `CurrentUser\My` её загружаемого профиля. В `domain_network` тот же служебный PFX с закрытым ключом импортируйте в профиль этой учётной записи на каждом узле Writer, включая `HLA-VIEW`: Writer в роли `mirror` читает исходные данные обычным расшифрованным потоком, и совпадения SID без ключа недостаточно. В `local_single_host` существует только один узел Writer.
2. Выпустите каждому оператору ровно один действующий EFS-сертификат и доставьте закрытый ключ в профиль пользователя утверждённым способом.
3. Сохраните служебный PFX, пользовательские PFX восстановления и парольные материалы вне файлового сервера по политике организации. Потеря единственного закрытого ключа может сделать данные невосстановимыми.
4. Для `domain_network` настройте отзыв, CRL и DRA/KRA по политике организации; DRA остаётся внешней доменной политикой GPO и не фиксируется в манифесте. Для `local_single_host` отдельный DRA обязателен: до EFS-фазы администратор создаёт сертификат и PFX восстановления, добавляет открытый сертификат в локальную политику восстановления EFS и переносит PFX с рабочего ПК в хранилище вне этого ПК.
5. До активации `local_single_host` выполните проверку восстановления на отдельном тестовом файле: зашифруйте его под служебной записью, удалите её доступную копию ключа из тестового профиля, восстановите файл только через DRA и задокументируйте результат. Инициализатор и приложение во время работы требуют непустой набор DRA на каждом EFS-файле, но конкретный хеш DRA в манифесте не фиксируется, поэтому контролируемая ротация остаётся административной процедурой.

Для создания и вывоза материала восстановления используйте штатный `cipher.exe /r:<базовое_имя>`, а для ключей службы и операторов — выпуск EFS-сертификата в `CurrentUser\My` соответствующей учётной записи и экспорт PFX штатным `cipher.exe /x` или оснасткой сертификатов. Пароли PFX и сами PFX не размещайте в корне, `%ProgramData%` приложения или каталоге Writer. Скрипты намеренно не создают и не хранят закрытые ключи: администратор отвечает за PKI, локальную политику восстановления и проверку восстановления; инициализатор отклоняет любой неоднозначный или неполный результат.

Скрипт `Export-HLA-EfsOperatorCertificates.ps1` предназначен только для `domain_network`: он проверяет рекурсивный состав группы AD, активность пользователей и наличие ровно одного текущего EFS-сертификата, а затем атомарно публикует новое поколение `.cer`, `operator-certificates.txt` и указатель `current.json`. В `local_single_host` передавайте инициализатору проверенные открытые `.cer` фактических операторов напрямую через `-OperatorCertificate 'COMPUTER\User=C:\...\user.cer'` для ПК в рабочей группе или без присоединения к домену либо `-OperatorCertificate 'DOMAIN\User=C:\...\user.cer'` для ПК, присоединённого к точному настроенному домену; скрипт экспорта из AD этот профиль явно отклоняет.

На `HLA-FB` независимо экспортируйте сертификаты операторов авторитетного корня:

```powershell
# HLA-FB; повышенный Windows PowerShell 5.1 администратора.
$ExportScript = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Export-HLA-EfsOperatorCertificates.ps1'
$ExportRoot = 'C:\ProgramData\HLA_Laboratory_System\HLA_EfsCertificates'
$OperatorGroup = '<AD_ГРУППА_EFS_ОПЕРАТОРОВ>'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
if ((@($OperatorGroup) + @($ServiceUser)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}

$PublishedCertificates = @(
    & $ExportScript -DeploymentMode domain_network `
        -Group $OperatorGroup -ExportRoot $ExportRoot `
        -ServiceUser $ServiceUser
)
if ($PublishedCertificates.Count -lt 1) {
    throw 'Экспорт не вернул ни одного сертификата оператора.'
}
$PublishedCertificates
```

На `HLA-VIEW` отдельным запуском экспортируйте сертификаты EFS-получателей зеркала. Это может быть более широкая группа Viewer-пользователей, но каждому её участнику всё равно нужен собственный действующий EFS-сертификат:

```powershell
# HLA-VIEW; повышенный Windows PowerShell 5.1 администратора.
$ExportScript = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Export-HLA-EfsOperatorCertificates.ps1'
$ExportRoot = 'C:\ProgramData\HLA_Laboratory_System\HLA_EfsCertificates'
$ViewerRecipientGroup = '<AD_ГРУППА_EFS_ПОЛУЧАТЕЛЕЙ_ЗЕРКАЛА>'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
if ((@($ViewerRecipientGroup) + @($ServiceUser)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}

$PublishedCertificates = @(
    & $ExportScript -DeploymentMode domain_network `
        -Group $ViewerRecipientGroup -ExportRoot $ExportRoot `
        -ServiceUser $ServiceUser
)
if ($PublishedCertificates.Count -lt 1) {
    throw 'Экспорт не вернул ни одного сертификата получателя зеркала.'
}
$PublishedCertificates
```

Экспорт открытых `.cer` не заменяет резервное копирование закрытых ключей.

## Предварительные условия корня

Инициализация выполняется только если:

- корень выведен из эксплуатации на всё время подготовки: приложения, Writer, задачи обслуживания и любые параллельные процессы записи остановлены до завершения всех фаз и итогового аудита;
- путь локальный, абсолютный, находится на фиксированном томе NTFS и не является корнем диска;
- сам корень и все его потомки не являются соединением каталогов, символической ссылкой, точкой монтирования или другой точкой повторной обработки;
- каждый файл имеет ровно одну жёсткую ссылку NTFS; наличие дополнительной ссылки, в том числе за пределами управляемого корня, отклоняется до шифрования или изменения ACL;
- точной корневой `.hla_internal` ещё нет;
- в ACL-режиме в дереве нет EFS-объектов;
- перед EFS-фазой в дереве также нет уже зашифрованных объектов;
- служебная учётная запись, читатели, группа Writer и сертификаты определяются однозначно.

Инициализатор самостоятельно создаёт новый `root_uuid`; задавать его вручную или копировать из другого корня запрещено.

## `local_single_host`: чистое развёртывание

До запуска скриптов администратор готовит одну фактическую служебную учётную запись, группы читателей и пользователей с правом обращения к Writer, а также локальный каталог на фиксированном томе NTFS. На ПК в рабочей группе или без присоединения к домену это выделенный локальный пользователь с именем из логического `FILE_BASE_SERVICE_USER` и локальные группы; на ПК, присоединённом к точному настроенному домену, используются соответствующая доменная запись и доменные группы, а одноимённые локальные записи не подставляются. Не включайте служебную запись в `Administrators` и не публикуйте корень как общий ресурс SMB. Для EFS-фазы заранее выдайте только этой служебной записи временный `FullControl` чистого корня, иначе она не сможет зашифровать дерево; завершающая ACL-фаза полностью заменит начальную ACL канонической политикой. Пересечение с любым неслужебным общим ресурсом SMB отклоняют повышенная предварительная проверка администратора, локальная фаза `Acl`, установщик Writer и установщик резервного копирования PostgreSQL; `Efs`/`Audit` и зарегистрированная задача резервного копирования под служебной записью намеренно не вызывают `Get-SmbShare`. Стандартные административные общие ресурсы остаются частью доверенной границы локальных администраторов и не являются каналом приложения. Инициализатор сам создаёт точные ACL и манифест, а для ACL назначает `SeBatchLogonRight` вместе с запретами интерактивного входа, входа через удалённый рабочий стол и сетевого входа; эти ACL и права не редактируют вручную. Убедитесь, что локальная политика или GPO ни через саму запись, ни через её группы не назначает `SeDenyBatchLogonRight` или `SeDenyServiceLogonRight`: прямой конфликт установщик обнаружит, а итоговый запуск приложения или службы остаётся обязательной проверкой действующей групповой политики.

Чистая инициализация ACL локального профиля из повышенного Windows PowerShell 5.1:

```powershell
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$Root = 'D:\HLA_Laboratory_System'
$ServiceUser = '<ЛОГИЧЕСКИЙ_ДОМЕН\USERNAME_ЛОКАЛЬНОЙ_СЛУЖБЫ>'
$Readers = @('<ЭФФЕКТИВНАЯ_ГРУППА_ЧТЕНИЯ>')
if ((@($ServiceUser) + @($Readers)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}

& $Init -Phase Acl -Root $Root -ServiceUser $ServiceUser `
    -DeploymentMode local_single_host -RootRole authoritative `
    -ReadAccount $Readers
& $Init -Phase Audit -Root $Root -ServiceUser $ServiceUser `
    -DeploymentMode local_single_host -RootRole authoritative
```

Для локального профиля EFS сначала настройте DRA с закрытым ключом вне рабочего ПК и выполните проверку восстановления, затем выпустите EFS-сертификаты службы и операторов. Непосредственно перед входом под служебной записью повышенный администратор обязан проверить, что корень не пересекается ни с одним обычным общим ресурсом SMB; повторная проверка автоматически выполняется в ACL-фазе, установщике Writer и установщике резервного копирования PostgreSQL. После активации не создавайте общий ресурс для корня, его предка или потомка: непрерывное соблюдение этого одноузлового инварианта относится к доверенной административной границе.

```powershell
# Повышенный Windows PowerShell 5.1 непосредственно перед EFS-фазой.
$Common = 'C:\Program Files\HLA_Laboratory_System\Scripts\Common\HLA_Operations_Common.psm1'
$Root = 'D:\HLA_Laboratory_System'
Import-Module -Name $Common -Force -ErrorAction Stop
$null = Assert-HlaLocalFileBaseRootNotShared -Root $Root
```

EFS-фазу выполняйте в загруженном профиле фактической служебной учётной записи: локальной `COMPUTER\User` на ПК в рабочей группе или без присоединения к домену либо доменной `DOMAIN\User` на ПК, присоединённом к точному настроенному домену; `ServiceUser` в аргументе всегда остаётся логическим `DOMAIN\User`, заданным на этапе сборки. Открытые `.cer` операторов указываются с фактическими именами учётных записей:

```powershell
# Windows PowerShell 5.1 под фактической служебной записью; без повышения прав.
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$Root = 'D:\HLA_Laboratory_System'
$ServiceUser = '<ЛОГИЧЕСКИЙ_ДОМЕН\USERNAME_ЛОКАЛЬНОЙ_СЛУЖБЫ>'
$Readers = @('<ЭФФЕКТИВНАЯ_ГРУППА_ЧТЕНИЯ>')
$Writers = @('<ЭФФЕКТИВНАЯ_ГРУППА_WRITER_CALLERS>')
$OperatorCertificates = @(
    '<ЭФФЕКТИВНАЯ_УЧЁТНАЯ_ЗАПИСЬ_ОПЕРАТОРА>=<ПОЛНЫЙ_ЛОКАЛЬНЫЙ_ПУТЬ_К_PUBLIC_CER>'
)
if ((@($ServiceUser) + @($Readers) + @($Writers) + @($OperatorCertificates)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}

& $Init -Phase Efs -Root $Root -ServiceUser $ServiceUser `
    -DeploymentMode local_single_host -EnableEncryption `
    -RootRole authoritative -ReadAccount $Readers -WriterAccount $Writers `
    -OperatorCertificate $OperatorCertificates
```

Затем выполните согласованную ACL-фазу из повышенного административного PowerShell:

```powershell
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$Root = 'D:\HLA_Laboratory_System'
$ServiceUser = '<ЛОГИЧЕСКИЙ_ДОМЕН\USERNAME_ЛОКАЛЬНОЙ_СЛУЖБЫ>'
$Readers = @('<ЭФФЕКТИВНАЯ_ГРУППА_ЧТЕНИЯ>')
$Writers = @('<ЭФФЕКТИВНАЯ_ГРУППА_WRITER_CALLERS>')
if ((@($ServiceUser) + @($Readers) + @($Writers)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}
& $Init -Phase Acl -Root $Root -ServiceUser $ServiceUser `
    -DeploymentMode local_single_host -EnableEncryption `
    -RootRole authoritative -ReadAccount $Readers -WriterAccount $Writers
```

После этого снова войдите под фактической служебной записью и выполните аудит:

```powershell
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$Root = 'D:\HLA_Laboratory_System'
$ServiceUser = '<ЛОГИЧЕСКИЙ_ДОМЕН\USERNAME_ЛОКАЛЬНОЙ_СЛУЖБЫ>'
& $Init -Phase Audit -Root $Root -ServiceUser $ServiceUser `
    -DeploymentMode local_single_host -EnableEncryption `
    -RootRole authoritative
```

Установите один Writer из повышенного административного PowerShell. `CanonicalServerName` намеренно не передаётся:

```powershell
$Installer = 'C:\Program Files\HLA_Laboratory_System\Scripts\Install-HLA-FileBaseWriterService.ps1'
$WriterExe = 'C:\Program Files\HLA_Laboratory_System\HLA_FileBase_Writer\HLA_FileBase_Writer.exe'
$Root = 'D:\HLA_Laboratory_System'
$ServiceUser = '<ЛОГИЧЕСКИЙ_ДОМЕН\USERNAME_ЛОКАЛЬНОЙ_СЛУЖБЫ>'
if ($ServiceUser -match '<[^>]+>') {
    throw 'Замените значение в угловых скобках.'
}
$Manifest = Get-Content -LiteralPath (Join-Path $Root '.hla_internal\protection.json') `
    -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$Manifest.deployment_mode -cne 'local_single_host' -or
    [string]$Manifest.root_role -cne 'authoritative' -or
    [string]$Manifest.protection_mode -cne 'efs') {
    throw 'Обнаружен не локальный авторитетный EFS-манифест.'
}
& $Installer -WriterExecutablePath $WriterExe -FileBaseRootPath $Root `
    -DeploymentMode local_single_host `
    -RootUuid ([Guid]::ParseExact([string]$Manifest.root_uuid, 'D')) `
    -RootRole authoritative -ProtectionMode efs `
    -FileBaseServiceUser $ServiceUser -Confirm:$false
```

Установщик создаёт конфигурацию Writer схемы 2 с `deployment_mode=local_single_host` и `canonical_server_name=null`, не добавляет зависимость от `LanmanServer`, назначает `SeServiceLogonRight` и локальные права запрета входа, а затем требует успешный локальный обмен HELLO с точным UUID. Для смены корня остановите приложения и службу, удалите регистрацию и защищённое состояние Writer по утверждённой процедуре, подготовьте новый чистый корень и выполните установку заново. Не переписывайте `writer.json`, `protection.json`, UUID или SID вручную.

Единственный локальный диск остаётся единой точкой отказа. Резервная копия PostgreSQL не заменяет резервную копию файлового дерева. Полную резервную копию EFS создавайте утверждённым средством Windows, сохраняющим исходный зашифрованный поток (`BackupRead`/`ReadEncryptedFileRaw` либо проверенный профиль `robocopy /EFSRAW`) на отдельный защищённый носитель. Обычное копирование из расшифрованного пользовательского потока такой резервной копией не считается. Периодически восстанавливайте отдельную тестовую копию на изолированный том NTFS и доказывайте чтение через DRA; исходный корень во время проверки не изменяйте.

Не выполняйте административный сброс пароля локальной служебной записи как штатную ротацию: он может лишить профиль доступа к закрытому ключу EFS, защищённому DPAPI. Плановая смена выполняется согласованно с сохранёнными PFX/DRA, обновлением SCM и задач, повторным аудитом ключа и проверкой восстановления. Удаление и повторное создание локального пользователя или группы меняет SID и требует нового чистого корня и нового развёртывания Writer; одноимённая новая запись не получает доступ к уже подготовленному корню.

Имя компьютера и состояние его присоединения к домену являются неизменяемой частью одноузлового развёртывания. Перед переименованием компьютера, присоединением к домену или выходом из него остановите приложение, Writer и задачу резервного копирования PostgreSQL, затем по утверждённой чистой процедуре удалите регистрации Writer и задачи вместе с их защищённым состоянием. После изменения идентичности узла создайте новый чистый корень и заново выполните полную подготовку учётных записей, ACL/EFS, манифеста, Writer и резервного копирования; сохранённые корень, конфигурацию и задачу повторно не используйте. Служба, зарегистрированная в SCM, и задача, зарегистрированная в Планировщике, хранят фактическую учётную запись и SID и сами не переоценивают состояние присоединения через механизм разрешения учётной записи, поэтому изменение идентичности узла всегда требует чистого повторного развёртывания.

## `domain_network`: инициализация ACL

Writer в ACL-режиме не устанавливается ни на `HLA-FB`, ни на `HLA-VIEW`. Оба следующих блока предназначены для чистых локальных каталогов и выполняются независимо на разных серверах.

Авторитетный корень инициализируйте локально на `HLA-FB` из **повышенного** Windows PowerShell 5.1:

```powershell
# HLA-FB; повышенный Windows PowerShell 5.1 администратора.
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$Root = 'D:\HLA_Laboratory_System'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
$Readers = @('<ДОМЕН\ГРУППА_ЧТЕНИЯ>')
if ((@($ServiceUser) + @($Readers)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}

& $Init -Phase Acl -Root $Root -ServiceUser $ServiceUser -DeploymentMode domain_network `
    -RootRole authoritative -ReadAccount $Readers

& $Init -Phase Audit -Root $Root -ServiceUser $ServiceUser -DeploymentMode domain_network `
    -RootRole authoritative
```

Получите `root_uuid` авторитетного ACL-корня безопасной командой только для чтения и перенесите выведенное значение в блок `HLA-VIEW`:

```powershell
# HLA-FB; повышенный Windows PowerShell 5.1 администратора; только чтение.
$ManifestPath = 'D:\HLA_Laboratory_System\.hla_internal\protection.json'
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$Manifest.root_role -cne 'authoritative' -or
    [string]$Manifest.protection_mode -cne 'acl') {
    throw 'Обнаружен не авторитетный ACL-манифест.'
}
$AuthoritativeUuid = [Guid]::ParseExact([string]$Manifest.root_uuid, 'D')
$AuthoritativeUuid.ToString('D')
```

Чистое ACL-зеркало инициализируйте локально на `HLA-VIEW` из **повышенного** Windows PowerShell 5.1. Этот блок не использует переменные из сеанса `HLA-FB`:

```powershell
# HLA-VIEW; повышенный Windows PowerShell 5.1 администратора.
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$MirrorRoot = 'D:\HLA-MIRROR'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
$AuthoritativeUuid = '<UUID_АВТОРИТЕТНОГО_КОРНЯ>'
$MirrorReaders = @('<ДОМЕН\ГРУППА_ЧТЕНИЯ_ЗЕРКАЛА>')
if ((@($ServiceUser) + @($AuthoritativeUuid) + @($MirrorReaders)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}
$AuthoritativeUuid = [Guid]::ParseExact($AuthoritativeUuid, 'D').ToString('D')

& $Init -Phase Acl -Root $MirrorRoot -ServiceUser $ServiceUser -DeploymentMode domain_network `
    -RootRole mirror -MirrorOfRootUuid $AuthoritativeUuid -ReadAccount $MirrorReaders

& $Init -Phase Audit -Root $MirrorRoot -ServiceUser $ServiceUser -DeploymentMode domain_network `
    -RootRole mirror -MirrorOfRootUuid $AuthoritativeUuid
```

После инициализации создайте отдельные общие ресурсы SMB, настройте разрешения общего ресурса и опубликуйте только канонические UNC. Приложение не должно использовать локальный `D:` даже на сервере хранения.

На `HLA-FB` и `HLA-VIEW` независимо подтвердите, что ACL-сценарий не содержит Writer. Блок только читает состояние SCM:

```powershell
# Выполнить отдельно на HLA-FB и HLA-VIEW; только чтение состояния SCM.
$UnexpectedWriter = Get-Service -Name 'HLAFileBaseWriter' -ErrorAction SilentlyContinue
if ($null -ne $UnexpectedWriter) {
    throw 'В ACL-режиме служба HLAFileBaseWriter должна отсутствовать.'
}
Write-Host 'Writer отсутствует, как и требуется для ACL-режима.'
```

## `domain_network`: инициализация EFS

EFS-фаза выполняется локально в загруженном профиле точного `ServiceUser`, где доступен его закрытый EFS-ключ. Не запускайте её под обычным администратором. ACL-фаза выполняется в отдельном **повышенном** административном PowerShell, а итоговый EFS-аудит — снова под `ServiceUser`. Не переносите переменные между окнами: каждый блок ниже заново задаёт и проверяет всё необходимое. После успешной EFS-фазы остаётся временная скрытая квитанция `.hla_internal\acl_bootstrap.json` с атрибутом «только чтение»: она не содержит секретов и намеренно не зашифрована, чтобы администратор мог до изменения ACL сверить физическую идентичность корня и манифеста, SID ролей и EFS-получателей. Не изменяйте и не удаляйте квитанцию вручную; успешная ACL-фаза удаляет её после проверки и применения ACL.

### Авторитетный EFS-корень на HLA-FB

Сначала войдите именно как `ServiceUser` и выполните EFS-фазу в обычном, не требующем повышения окне Windows PowerShell 5.1. Блок читает опубликованный ранее `operator-certificates.txt` с этого же сервера:

```powershell
# HLA-FB; Windows PowerShell 5.1 в загруженном профиле ServiceUser.
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$Root = 'D:\HLA_Laboratory_System'
$ExportRoot = 'C:\ProgramData\HLA_Laboratory_System\HLA_EfsCertificates'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
$Readers = @('<ДОМЕН\ГРУППА_ЧТЕНИЯ>')
$Writers = @('<ДОМЕН\ГРУППА_ОПЕРАТОРОВ_ЗАПИСИ>')
if ((@($ServiceUser) + @($Readers) + @($Writers)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}

$ExpectedSid = ([System.Security.Principal.NTAccount]$ServiceUser).Translate(
    [System.Security.Principal.SecurityIdentifier]
).Value
$CurrentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($CurrentSid -cne $ExpectedSid) {
    throw "Этот блок должен выполняться под ServiceUser с SID $ExpectedSid."
}
$Pointer = Get-Content -LiteralPath (Join-Path $ExportRoot 'current.json') `
    -Raw -Encoding UTF8 | ConvertFrom-Json
$GenerationId = [Guid]::ParseExact([string]$Pointer.generation_id, 'D').ToString('D')
$CertificateList = Join-Path (Join-Path (Join-Path $ExportRoot 'generations') $GenerationId) `
    'operator-certificates.txt'
$OperatorCertificates = @(
    Get-Content -LiteralPath $CertificateList -Encoding ASCII |
        Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
)
if ($OperatorCertificates.Count -lt 1 -or $OperatorCertificates -match '<[^>]+>') {
    throw 'Опубликованный список сертификатов пуст или содержит подстановочное значение.'
}

& $Init -Phase Efs -Root $Root -ServiceUser $ServiceUser `
    -DeploymentMode domain_network -EnableEncryption `
    -RootRole authoritative -ReadAccount $Readers -WriterAccount $Writers `
    -OperatorCertificate $OperatorCertificates
```

Закройте окно `ServiceUser`. Затем выполните ACL-фазу из нового **повышенного** Windows PowerShell 5.1 администратора. Значения `Readers` и `Writers` должны точно совпадать со значениями EFS-фазы; несоответствие временной квитанции отклоняется до первого изменения ACL:

```powershell
# HLA-FB; повышенный Windows PowerShell 5.1 администратора.
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$Root = 'D:\HLA_Laboratory_System'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
$Readers = @('<ДОМЕН\ГРУППА_ЧТЕНИЯ>')
$Writers = @('<ДОМЕН\ГРУППА_ОПЕРАТОРОВ_ЗАПИСИ>')
if ((@($ServiceUser) + @($Readers) + @($Writers)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}

& $Init -Phase Acl -Root $Root -ServiceUser $ServiceUser `
    -DeploymentMode domain_network -EnableEncryption `
    -RootRole authoritative -ReadAccount $Readers -WriterAccount $Writers
```

Закройте административное окно, снова войдите именно как `ServiceUser` и выполните итоговый аудит только для чтения:

```powershell
# HLA-FB; Windows PowerShell 5.1 в загруженном профиле ServiceUser; только аудит.
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$Root = 'D:\HLA_Laboratory_System'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
if ($ServiceUser -match '<[^>]+>') {
    throw 'Замените значение в угловых скобках.'
}

$ExpectedSid = ([System.Security.Principal.NTAccount]$ServiceUser).Translate(
    [System.Security.Principal.SecurityIdentifier]
).Value
$CurrentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($CurrentSid -cne $ExpectedSid) {
    throw "Этот блок должен выполняться под ServiceUser с SID $ExpectedSid."
}
& $Init -Phase Audit -Root $Root -ServiceUser $ServiceUser `
    -DeploymentMode domain_network -EnableEncryption `
    -RootRole authoritative
```

Получите UUID авторитетного EFS-корня, успешно прошедшего аудит, безопасной командой только для чтения и перенесите выведенное значение на `HLA-VIEW`:

```powershell
# HLA-FB; ServiceUser; только чтение.
$ManifestPath = 'D:\HLA_Laboratory_System\.hla_internal\protection.json'
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$Manifest.root_role -cne 'authoritative' -or
    [string]$Manifest.protection_mode -cne 'efs') {
    throw 'Обнаружен не авторитетный EFS-манифест.'
}
$AuthoritativeUuid = [Guid]::ParseExact([string]$Manifest.root_uuid, 'D')
$AuthoritativeUuid.ToString('D')
```

### EFS-зеркало на HLA-VIEW

EFS-зеркало инициализируется независимо. До запуска импортируйте в загружаемый профиль `ServiceUser` на `HLA-VIEW` тот же служебный PFX, которым зашифрованы файлы источника. Политика чтения зеркала может быть шире политики авторитетного корня, например включать отдельную группу всех Viewer-пользователей; это не выдаёт им доступ к источнику. `WriterAccount` зеркалу не передаётся и `writer_sids` остаётся пустым.

Войдите на `HLA-VIEW` именно как `ServiceUser` и выполните EFS-фазу в обычном Windows PowerShell 5.1:

```powershell
# HLA-VIEW; Windows PowerShell 5.1 в загруженном профиле ServiceUser.
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$MirrorRoot = 'D:\HLA-MIRROR'
$ExportRoot = 'C:\ProgramData\HLA_Laboratory_System\HLA_EfsCertificates'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
$AuthoritativeUuid = '<UUID_АВТОРИТЕТНОГО_КОРНЯ>'
$MirrorReaders = @('<ДОМЕН\ГРУППА_ЧТЕНИЯ_ЗЕРКАЛА>')
if ((@($ServiceUser) + @($AuthoritativeUuid) + @($MirrorReaders)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}
$AuthoritativeUuid = [Guid]::ParseExact($AuthoritativeUuid, 'D').ToString('D')

$ExpectedSid = ([System.Security.Principal.NTAccount]$ServiceUser).Translate(
    [System.Security.Principal.SecurityIdentifier]
).Value
$CurrentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($CurrentSid -cne $ExpectedSid) {
    throw "Этот блок должен выполняться под ServiceUser с SID $ExpectedSid."
}
$Pointer = Get-Content -LiteralPath (Join-Path $ExportRoot 'current.json') `
    -Raw -Encoding UTF8 | ConvertFrom-Json
$GenerationId = [Guid]::ParseExact([string]$Pointer.generation_id, 'D').ToString('D')
$CertificateList = Join-Path (Join-Path (Join-Path $ExportRoot 'generations') $GenerationId) `
    'operator-certificates.txt'
$MirrorCertificates = @(
    Get-Content -LiteralPath $CertificateList -Encoding ASCII |
        Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
)
if ($MirrorCertificates.Count -lt 1 -or $MirrorCertificates -match '<[^>]+>') {
    throw 'Опубликованный список сертификатов пуст или содержит подстановочное значение.'
}

& $Init -Phase Efs -Root $MirrorRoot -ServiceUser $ServiceUser `
    -DeploymentMode domain_network -EnableEncryption `
    -RootRole mirror -MirrorOfRootUuid $AuthoritativeUuid `
    -ReadAccount $MirrorReaders -OperatorCertificate $MirrorCertificates
```

Закройте окно `ServiceUser`. Затем выполните ACL-фазу на `HLA-VIEW` из нового **повышенного** Windows PowerShell 5.1 администратора; параметры сверяются с временной квитанцией до первого изменения ACL:

```powershell
# HLA-VIEW; повышенный Windows PowerShell 5.1 администратора.
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$MirrorRoot = 'D:\HLA-MIRROR'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
$AuthoritativeUuid = '<UUID_АВТОРИТЕТНОГО_КОРНЯ>'
$MirrorReaders = @('<ДОМЕН\ГРУППА_ЧТЕНИЯ_ЗЕРКАЛА>')
if ((@($ServiceUser) + @($AuthoritativeUuid) + @($MirrorReaders)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}
$AuthoritativeUuid = [Guid]::ParseExact($AuthoritativeUuid, 'D').ToString('D')

& $Init -Phase Acl -Root $MirrorRoot -ServiceUser $ServiceUser `
    -DeploymentMode domain_network -EnableEncryption `
    -RootRole mirror -MirrorOfRootUuid $AuthoritativeUuid -ReadAccount $MirrorReaders
```

Закройте административное окно, снова войдите именно как `ServiceUser` и выполните итоговый аудит только для чтения:

```powershell
# HLA-VIEW; Windows PowerShell 5.1 в загруженном профиле ServiceUser; только аудит.
$Init = 'C:\Program Files\HLA_Laboratory_System\Scripts\Encryption\Initialize-HLA-FileBaseEncryption.ps1'
$MirrorRoot = 'D:\HLA-MIRROR'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
$AuthoritativeUuid = '<UUID_АВТОРИТЕТНОГО_КОРНЯ>'
if ((@($ServiceUser) + @($AuthoritativeUuid)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}
$AuthoritativeUuid = [Guid]::ParseExact($AuthoritativeUuid, 'D').ToString('D')

$ExpectedSid = ([System.Security.Principal.NTAccount]$ServiceUser).Translate(
    [System.Security.Principal.SecurityIdentifier]
).Value
$CurrentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($CurrentSid -cne $ExpectedSid) {
    throw "Этот блок должен выполняться под ServiceUser с SID $ExpectedSid."
}
& $Init -Phase Audit -Root $MirrorRoot -ServiceUser $ServiceUser `
    -DeploymentMode domain_network -EnableEncryption `
    -RootRole mirror -MirrorOfRootUuid $AuthoritativeUuid
```

Не копируйте `.hla_internal`, `protection.json`, UUID, EFS-метаданные авторитетного корня или его журнал на зеркало. Штатная публикация исключает только корневую `.hla_internal`; каталоги `<ОРГАН>\.source_files` копируются вместе с атрибутом `Hidden`. Затем Writer применяет к обычным файлам политику EFS зеркала и проверяет её.

## `domain_network`: установка Writer для EFS

Этот раздел применяется **только** при `ENABLE_FILE_BASE_ENCRYPTION=True`. При `False` Writer устанавливать нельзя: Laboratory работает через ACL-контур, а зеркало публикуется без службы Writer.

Лицензиар собирает отдельную Официальную сборку без пользовательского интерфейса командой из `requirements.txt`. Получатель размещает непосредственно переданную ему автономную папку, указанную в отдельном адресном разрешении этого Writer-ПК, точно здесь:

```text
C:\Program Files\HLA_Laboratory_System\HLA_FileBase_Writer\
    HLA_FileBase_Writer.exe
    ... соседние DLL и компоненты автономной сборки
```

GUI собирается первой командой, Writer — второй: повторный запуск GUI-команды с `--remove-output` удалит вложенную Writer-папку, поэтому после пересборки GUI Writer всегда собирается заново.

После успешного аудита схемы 4 установите авторитетный Writer локально на `HLA-FB` из **повышенного** Windows PowerShell 5.1. Установщик запросит пароль точного `ServiceUser` для SCM:

```powershell
# HLA-FB; повышенный Windows PowerShell 5.1 администратора.
$Installer = 'C:\Program Files\HLA_Laboratory_System\Scripts\Install-HLA-FileBaseWriterService.ps1'
$WriterExe = 'C:\Program Files\HLA_Laboratory_System\HLA_FileBase_Writer\HLA_FileBase_Writer.exe'
$Root = 'D:\HLA_Laboratory_System'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
if ($ServiceUser -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}
$ManifestPath = Join-Path $Root '.hla_internal\protection.json'
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$Manifest.root_role -cne 'authoritative' -or
    [string]$Manifest.protection_mode -cne 'efs') {
    throw 'Writer можно установить только для авторитетного EFS-корня, успешно прошедшего аудит.'
}
$RootUuid = [Guid]::ParseExact([string]$Manifest.root_uuid, 'D')

& $Installer `
    -WriterExecutablePath $WriterExe `
    -FileBaseRootPath $Root `
    -DeploymentMode domain_network `
    -RootUuid $RootUuid `
    -RootRole authoritative `
    -ProtectionMode efs `
    -CanonicalServerName 'HLA-FB' `
    -FileBaseServiceUser $ServiceUser `
    -Confirm:$false
```

На `HLA-VIEW` независимо установите Writer роли `mirror` из **повышенного** Windows PowerShell 5.1. Блок читает `root_uuid` именно локального зеркала и не использует переменные из сеанса `HLA-FB`:

```powershell
# HLA-VIEW; повышенный Windows PowerShell 5.1 администратора.
$Installer = 'C:\Program Files\HLA_Laboratory_System\Scripts\Install-HLA-FileBaseWriterService.ps1'
$WriterExe = 'C:\Program Files\HLA_Laboratory_System\HLA_FileBase_Writer\HLA_FileBase_Writer.exe'
$MirrorRoot = 'D:\HLA-MIRROR'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
$AuthoritativeUuid = '<UUID_АВТОРИТЕТНОГО_КОРНЯ>'
if ((@($ServiceUser) + @($AuthoritativeUuid)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}
$AuthoritativeUuid = [Guid]::ParseExact($AuthoritativeUuid, 'D').ToString('D')

$ManifestPath = Join-Path $MirrorRoot '.hla_internal\protection.json'
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$Manifest.root_role -cne 'mirror' -or
    [string]$Manifest.protection_mode -cne 'efs' -or
    [string]$Manifest.mirror_of_root_uuid -cne $AuthoritativeUuid) {
    throw 'Обнаружен EFS-манифест не того зеркала или не того авторитетного корня.'
}
$RootUuid = [Guid]::ParseExact([string]$Manifest.root_uuid, 'D')

& $Installer `
    -WriterExecutablePath $WriterExe `
    -FileBaseRootPath $MirrorRoot `
    -DeploymentMode domain_network `
    -RootUuid $RootUuid `
    -RootRole mirror `
    -ProtectionMode efs `
    -CanonicalServerName 'HLA-VIEW' `
    -FileBaseServiceUser $ServiceUser `
    -Confirm:$false
```

Установщик поддерживает только чистую установку: до запуска не должно быть ни службы, ни защищённого `%ProgramData%\HLA_Laboratory_System\HLA_FileBase_Writer`. Он не копирует дистрибутив и не создаёт общий ресурс SMB, DNS или SPN. Пароль запрашивается один раз для регистрации SCM; он не записывается в конфигурацию Writer.

Рабочая конфигурация и ограниченный журнал находятся в защищённом `C:\ProgramData\HLA_Laboratory_System\HLA_FileBase_Writer`; пользовательские данные и журнал файловых операций туда не переносятся. SCM запускает один процесс без пользовательского интерфейса, Qt и учётных данных PostgreSQL. Обновление Writer выполняется отдельно от средства обновления Viewer только во время административного окна обслуживания с остановленной службой.

После установки проверьте `HLA-FB` из **повышенного** PowerShell. Блок не перезапускает службу и не изменяет конфигурацию или файловую базу:

```powershell
# HLA-FB; повышенный PowerShell администратора; только чтение.
$Root = 'D:\HLA_Laboratory_System'
$ConfigPath = 'C:\ProgramData\HLA_Laboratory_System\HLA_FileBase_Writer\writer.json'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
if ($ServiceUser -match '<[^>]+>') {
    throw 'Замените значение в угловых скобках.'
}

$Service = Get-CimInstance -ClassName Win32_Service `
    -Filter "Name='HLAFileBaseWriter'" -ErrorAction Stop
$Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$Manifest = Get-Content -LiteralPath (Join-Path $Root '.hla_internal\protection.json') `
    -Raw -Encoding UTF8 | ConvertFrom-Json
if ($null -eq $Service -or [string]$Service.State -cne 'Running' -or
    [string]$Service.StartName -ine $ServiceUser -or
    [uint64]$Config.schema_version -ne 2 -or
    [string]$Config.deployment_mode -cne 'domain_network' -or
    [string]$Config.root_path -ine $Root -or
    [string]$Config.expected_root_role -cne 'authoritative' -or
    [string]$Config.expected_protection_mode -cne 'efs' -or
    [string]$Config.canonical_server_name -cne 'hla-fb' -or
    [string]$Config.expected_root_uuid -cne [string]$Manifest.root_uuid) {
    throw 'Служба или защищённая конфигурация HLA-FB не соответствует EFS-манифесту.'
}
Write-Host 'Авторитетный Writer запущен и соответствует локальному EFS-корню.'
```

На `HLA-VIEW` выполните независимую проверку только для чтения:

```powershell
# HLA-VIEW; повышенный PowerShell администратора; только чтение.
$MirrorRoot = 'D:\HLA-MIRROR'
$ConfigPath = 'C:\ProgramData\HLA_Laboratory_System\HLA_FileBase_Writer\writer.json'
$ServiceUser = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_СЛУЖБЫ>'
$AuthoritativeUuid = '<UUID_АВТОРИТЕТНОГО_КОРНЯ>'
if ((@($ServiceUser) + @($AuthoritativeUuid)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}
$AuthoritativeUuid = [Guid]::ParseExact($AuthoritativeUuid, 'D').ToString('D')

$Service = Get-CimInstance -ClassName Win32_Service `
    -Filter "Name='HLAFileBaseWriter'" -ErrorAction Stop
$Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$Manifest = Get-Content -LiteralPath (Join-Path $MirrorRoot '.hla_internal\protection.json') `
    -Raw -Encoding UTF8 | ConvertFrom-Json
if ($null -eq $Service -or [string]$Service.State -cne 'Running' -or
    [string]$Service.StartName -ine $ServiceUser -or
    [uint64]$Config.schema_version -ne 2 -or
    [string]$Config.deployment_mode -cne 'domain_network' -or
    [string]$Config.root_path -ine $MirrorRoot -or
    [string]$Config.expected_root_role -cne 'mirror' -or
    [string]$Config.expected_protection_mode -cne 'efs' -or
    [string]$Config.canonical_server_name -cne 'hla-view' -or
    [string]$Config.expected_root_uuid -cne [string]$Manifest.root_uuid -or
    [string]$Manifest.mirror_of_root_uuid -cne $AuthoritativeUuid) {
    throw 'Служба или защищённая конфигурация HLA-VIEW не соответствует EFS-манифесту зеркала.'
}
Write-Host 'Writer роли mirror запущен и соответствует локальному EFS-зеркалу.'
```

## `domain_network`: сетевые предварительные условия

До запуска клиентов администратор отдельно настраивает:

- псевдонимы DNS `HLA-FB` и `HLA-VIEW`;
- SPN `cifs/<canonical-name>` без неоднозначных владельцев;
- SMB 3.x с обязательным шифрованием и точные значения `REG_DWORD` в `HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters` на каждом серверном узле Writer (`HLA-FB` и `HLA-VIEW` в EFS-режиме): `SMB1=0`, `SMB2=1`, `EncryptData=1`, `RejectUnencryptedAccess=1`;
- политика Windows Hardened UNC Paths с `RequireMutualAuthentication=1`, `RequireIntegrity=1` и `RequirePrivacy=1` для каждой точной конечной точки: `\\HLA-FB\HLA_Laboratory_System`, `\\HLA-VIEW\HLA_Laboratory_System` и, на клиентах Laboratory EFS, `\\HLA-FB\IPC$` для именованного канала Writer. Локальный запуск зеркала использует `\\.\pipe` и не требует `\\HLA-VIEW\IPC$`;
- права чтения общего ресурса SMB и NTFS для настроенных групп при отсутствии клиентского доступа на запись в EFS-режиме;
- межсетевой экран для SMB и именованного канала;
- одинаковое каноническое UNC во всех сборках одного физического корня.

Подключённый диск, IP-адрес, имя конкретного временного узла, SMB 1/2, DFS-R или иной многоведущий режим и общий ресурс SMB с включённым свойством `ContinuouslyAvailable` для EFS не поддерживаются. Приложение проверяет установленное SMB-соединение и отказывает в доступе при неоднозначной или ослабленной конфигурации.

## Безопасная проверка ролей

Следующие проверки ничего не создают, не переименовывают и не удаляют. Они читают манифест и ACL, а указанный существующий обычный файл открывают только с `FileAccess.Read`. Проверка отсутствия прямой записи выполняется анализом канонической ACL, без пробной записи в рабочую файловую базу. Первый блок применяется к Laboratory в обоих профилях и к локальному Viewer в `local_single_host`; второй блок относится только к отдельному Viewer-зеркалу `domain_network`.

Обычное приложение проверяет корень, манифест, доступную читателям часть `.hla_internal` и точный состав её верхнего уровня, но не входит в закрытые `transactions` и `locks` и не открывает `writer.lock`. Тип и ACL закрытых каталогов и точный состав `locks` проверяются только под полномочиями служебной учётной записи: при активации узла Writer, служебном чтении источника зеркала и непосредственно перед допуском к изменению. Сам `writer.lock` эксклюзивно открывается и проверяется при активации и допуске к изменению; во время изменения повторно проверяется тот же удерживаемый дескриптор без нового открытия файла. Поэтому Viewer не получает служебный токен или доступ к Writer, а чтение не зависит от занятости `writer.lock`. Отсутствие обязательного верхнеуровневого объекта блокирует даже чтение, а нарушение, видимое только внутри закрытого контура, блокирует служебное чтение или изменение до первой операции с пользовательскими данными.

На Laboratory-ПК выполните блок под обычным оператором без локальных административных прав. Для EFS Laboratory он дополнительно подтверждает членство токена в `writer_sids`; это разрешение вызвать Writer, а не право прямой записи. Для локального Viewer укажите `viewer`: его токен, напротив, не должен входить в `writer_sids`. В ACL-режиме `writer_sids` по определению пуст. В `domain_network` ACL-запись идёт через настроенный SMB-сеанс, а в `local_single_host` — под фактической служебной записью на том же ПК:

```powershell
# Laboratory либо локальный Viewer; обычный пользователь; только чтение.
$DeploymentMode = '<domain_network ИЛИ local_single_host>'
$Capability = '<laboratory ИЛИ viewer>'
$Root = '<КАНОНИЧЕСКИЙ_UNC_ИЛИ_ЛОКАЛЬНЫЙ_AUTHORITATIVE_ROOT>'
$ExpectedAccount = '<ЭФФЕКТИВНАЯ_УЧЁТНАЯ_ЗАПИСЬ_ПОЛЬЗОВАТЕЛЯ>'
$ExpectedMode = '<acl ИЛИ efs>'
$ExistingRelativeFile = '<ОТНОСИТЕЛЬНЫЙ_ПУТЬ_СУЩЕСТВУЮЩЕГО_ОБЫЧНОГО_ФАЙЛА>'
if ((@($DeploymentMode) + @($Capability) + @($Root) + @($ExpectedAccount) +
    @($ExpectedMode) + @($ExistingRelativeFile)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}
if ($DeploymentMode -notin @('domain_network', 'local_single_host') -or
    $Capability -notin @('laboratory', 'viewer') -or
    ($DeploymentMode -eq 'domain_network' -and $Capability -eq 'viewer') -or
    $ExpectedMode -notin @('acl', 'efs') -or
    [IO.Path]::IsPathRooted($ExistingRelativeFile) -or
    $ExistingRelativeFile -match '(^|[\\/])\.\.([\\/]|$)') {
    throw 'Укажите допустимые профиль развёртывания, назначение клиента, режим защиты и безопасный относительный путь.'
}

$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$ExpectedSid = ([System.Security.Principal.NTAccount]$ExpectedAccount).Translate(
    [System.Security.Principal.SecurityIdentifier]
).Value
if ($Identity.User.Value -cne $ExpectedSid) {
    throw "Проверка должна выполняться под $ExpectedAccount."
}
$TokenSids = @($Identity.User.Value) + @($Identity.Groups | ForEach-Object { $_.Value })
$Manifest = Get-Content -LiteralPath (Join-Path $Root '.hla_internal\protection.json') `
    -Raw -Encoding UTF8 | ConvertFrom-Json
if ([uint64]$Manifest.schema_version -ne 4 -or
    [string]$Manifest.deployment_mode -cne $DeploymentMode -or
    [string]$Manifest.root_role -cne 'authoritative' -or
    $null -ne $Manifest.mirror_of_root_uuid -or
    [string]$Manifest.protection_mode -cne $ExpectedMode) {
    throw 'Схема, профиль, роль или режим authoritative-корня не совпадают со сборкой.'
}
if (@($Manifest.reader_sids | Where-Object { [string]$_ -in $TokenSids }).Count -lt 1) {
    throw 'Токен оператора не входит ни в одну группу reader_sids.'
}
if ($ExpectedMode -eq 'efs') {
    $HasWriterCapability = @(
        $Manifest.writer_sids | Where-Object { [string]$_ -in $TokenSids }
    ).Count -gt 0
    if ($Capability -eq 'laboratory' -and -not $HasWriterCapability) {
        throw 'Токен EFS Laboratory-оператора не входит ни в одну группу writer_sids.'
    }
    if ($Capability -eq 'viewer' -and $HasWriterCapability) {
        throw 'Токен локального Viewer не должен входить в writer_sids.'
    }
}
elseif (@($Manifest.writer_sids).Count -ne 0) {
    throw 'У ACL-корня поле writer_sids должно быть пустым.'
}

$WriteMask = [System.Security.AccessControl.FileSystemRights]::WriteData -bor
    [System.Security.AccessControl.FileSystemRights]::AppendData -bor
    [System.Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
    [System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
    [System.Security.AccessControl.FileSystemRights]::WriteAttributes -bor
    [System.Security.AccessControl.FileSystemRights]::Delete -bor
    [System.Security.AccessControl.FileSystemRights]::ChangePermissions -bor
    [System.Security.AccessControl.FileSystemRights]::TakeOwnership
$Rules = (Get-Acl -LiteralPath $Root).GetAccessRules(
    $true, $true, [System.Security.Principal.SecurityIdentifier]
)
$DirectWriteRules = @($Rules | Where-Object {
    $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
    $_.IdentityReference.Value -in $TokenSids -and
    ($_.FileSystemRights -band $WriteMask) -ne 0
})
if ($DirectWriteRules.Count -ne 0) {
    throw 'Токен оператора имеет прямое разрешение записи в корень.'
}

$Target = Get-Item -LiteralPath (Join-Path $Root $ExistingRelativeFile) -Force
if ($Target.PSIsContainer -or ($Target.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Для проверки нужен существующий обычный файл без точки повторной обработки.'
}
$Share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
$Stream = [IO.File]::Open($Target.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, $Share)
try { [void]$Stream.ReadByte() }
finally { $Stream.Dispose() }
Write-Host "Авторитетный корень читается без прямой ACE записи: $($Identity.Name)"
```

На Viewer-ПК `domain_network` выполните независимый блок под обычным Viewer-пользователем без локальных административных прав. Для зеркала при любом режиме `writer_sids` должен быть пуст:

```powershell
# Viewer-ПК; обычный Viewer-пользователь; только чтение.
$Root = '\\HLA-VIEW\HLA_Laboratory_System'
$ExpectedAccount = '<ДОМЕН\УЧЁТНАЯ_ЗАПИСЬ_VIEWER_ПОЛЬЗОВАТЕЛЯ>'
$ExpectedMode = '<acl ИЛИ efs>'
$ExistingRelativeFile = '<ОТНОСИТЕЛЬНЫЙ_ПУТЬ_СУЩЕСТВУЮЩЕГО_ОБЫЧНОГО_ФАЙЛА>'
if ((@($ExpectedAccount) + @($ExpectedMode) + @($ExistingRelativeFile)) -match '<[^>]+>') {
    throw 'Замените значения в угловых скобках.'
}
if ($ExpectedMode -notin @('acl', 'efs') -or
    [IO.Path]::IsPathRooted($ExistingRelativeFile) -or
    $ExistingRelativeFile -match '(^|[\\/])\.\.([\\/]|$)') {
    throw 'Укажите режим acl/efs и безопасный относительный путь существующего файла.'
}

$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$ExpectedSid = ([System.Security.Principal.NTAccount]$ExpectedAccount).Translate(
    [System.Security.Principal.SecurityIdentifier]
).Value
if ($Identity.User.Value -cne $ExpectedSid) {
    throw "Проверка должна выполняться под $ExpectedAccount."
}
$TokenSids = @($Identity.User.Value) + @($Identity.Groups | ForEach-Object { $_.Value })
$Manifest = Get-Content -LiteralPath (Join-Path $Root '.hla_internal\protection.json') `
    -Raw -Encoding UTF8 | ConvertFrom-Json
if ([uint64]$Manifest.schema_version -ne 4 -or
    [string]$Manifest.deployment_mode -cne 'domain_network' -or
    [string]$Manifest.root_role -cne 'mirror' -or
    [string]$Manifest.protection_mode -cne $ExpectedMode -or
    [string]::IsNullOrWhiteSpace([string]$Manifest.mirror_of_root_uuid)) {
    throw 'Роль, режим или связь зеркала не совпадает со сборкой Viewer.'
}
if (@($Manifest.writer_sids).Count -ne 0) {
    throw 'У зеркала поле writer_sids должно быть пустым.'
}
if (@($Manifest.reader_sids | Where-Object { [string]$_ -in $TokenSids }).Count -lt 1) {
    throw 'Токен Viewer-пользователя не входит ни в одну группу reader_sids.'
}

$WriteMask = [System.Security.AccessControl.FileSystemRights]::WriteData -bor
    [System.Security.AccessControl.FileSystemRights]::AppendData -bor
    [System.Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
    [System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
    [System.Security.AccessControl.FileSystemRights]::WriteAttributes -bor
    [System.Security.AccessControl.FileSystemRights]::Delete -bor
    [System.Security.AccessControl.FileSystemRights]::ChangePermissions -bor
    [System.Security.AccessControl.FileSystemRights]::TakeOwnership
$Rules = (Get-Acl -LiteralPath $Root).GetAccessRules(
    $true, $true, [System.Security.Principal.SecurityIdentifier]
)
$DirectWriteRules = @($Rules | Where-Object {
    $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
    $_.IdentityReference.Value -in $TokenSids -and
    ($_.FileSystemRights -band $WriteMask) -ne 0
})
if ($DirectWriteRules.Count -ne 0) {
    throw 'Токен Viewer-пользователя имеет прямое разрешение записи в зеркало.'
}

$Target = Get-Item -LiteralPath (Join-Path $Root $ExistingRelativeFile) -Force
if ($Target.PSIsContainer -or ($Target.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Для проверки нужен существующий обычный файл без точки повторной обработки.'
}
$Share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
$Stream = [IO.File]::Open($Target.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, $Share)
try { [void]$Stream.ReadByte() }
finally { $Stream.Dispose() }
Write-Host "Чтение разрешено, прямое разрешение записи отсутствует: $($Identity.Name)"
```

## Проверка и обслуживание

Перед вводом в эксплуатацию выполните общий список:

1. `Audit` успешно завершается под требуемой фактической учётной записью на каждом физическом корне: на двух корнях `domain_network` либо на единственном корне роли `authoritative` в `local_single_host`.
2. Каждый манифест имеет схему 4 и точный `deployment_mode`, заданный на этапе сборки; использование корня другим профилем отклоняется.
3. В `domain_network` у корней разные `root_uuid`, `mirror_of_root_uuid` зеркала точно равен UUID источника, а опубликованный файл получает политику ACL/EFS именно зеркала. В `local_single_host` манифест имеет роль `authoritative`, `mirror_of_root_uuid=null`, корень не пересекается с общим ресурсом SMB и зеркало файловой базы отсутствует.
4. Приведённые выше проверки только для чтения проходят под обычными Laboratory- и применимыми Viewer-пользователями; локальный Viewer читает тот же корень роли `authoritative` и не входит в `writer_sids`.
5. Закрытие клиента не изменяет постоянную ACL корня; временные операторские ACE не создаются.
6. Потеря обязательной для профиля блокировки, Writer, корня или сетевого канала приводит к безопасному отказу, а не к параллельной записи или автоматическому переключению профиля.

Дополнительно только для ACL (`ENABLE_FILE_BASE_ENCRYPTION=False`):

1. Служба `HLAFileBaseWriter` отсутствует на обоих серверных корнях `domain_network` либо на единственном ПК `local_single_host`.
2. В `domain_network` Laboratory-сборка записывает только через настроенный SMB-сеанс `FILE_BASE_SERVICE_USER`; в `local_single_host` — в локальный корень роли `authoritative` под фактической служебной учётной записью, сопоставленной с `FILE_BASE_SERVICE_USER`. Обычный оператор не получает прямую ACE записи.
3. Viewer `domain_network` читает опубликованное ACL-зеркало; локальный Viewer читает общий корень роли `authoritative`. Ни один Viewer не может инициировать лабораторную запись.

Дополнительно только для EFS (`ENABLE_FILE_BASE_ENCRYPTION=True`):

1. В `domain_network` Writer находится в состоянии `Running` на `HLA-FB` с ролью `authoritative` и на `HLA-VIEW` с ролью `mirror`; на Laboratory- и Viewer-ПК Writer отсутствует. В одноузловом Laboratory-профиле установлен ровно один локальный Writer роли `authoritative`; отдельного Writer для Viewer нет.
2. Laboratory-оператор читает корень роли `authoritative` своим EFS-ключом, не имеет прямой ACE записи и может инициировать запись только через авторитетный Writer.
3. В `domain_network` Writer роли `mirror` под фактической учётной записью `ServiceUser` может расшифровать тестовый исходный файл тем же закрытым служебным ключом; одного совпадения SID недостаточно. В `local_single_host` отдельно доказано восстановление тестового файла через DRA, закрытый ключ которого хранится вне рабочего ПК.
4. Viewer читает разрешённый для профиля корень своим EFS-ключом и не может инициировать лабораторную запись.
5. Потеря Writer, обязательного сетевого канала или блокировки PostgreSQL приводит к безопасному отказу, а не к параллельной записи.

### Журнал и аварийное восстановление

Для транзакционных операций импорта, замены, удаления и переименования состояния исследования (`test-state`), а также автономного импорта и восстановления до первого фактического изменения публикуется долговечная запись `PREPARED` с не содержащей секретов картой файлового отката, а после определения результата — итоговая запись. Произвольные операции Root Explorer сохраняют согласованную пользовательскую семантику возможного частичного результата и не объявляются транзакционными, но общий допуск по блокировкам и журналу восстановления не обходят.

Записи `PREPARED` и `ROLLBACK_PENDING` в `.hla_internal\transactions` блокируют новые изменения и зеркалирование. Не удаляйте их вручную. Ответственный администратор сверяет указанные журналом точки подключения PostgreSQL и файловое состояние, затем запускает приложение с графическим интерфейсом и повышенными правами:

```powershell
HLA_Laboratory_System.exe --file-base-recovery list
```

Без параметра `--root` команда использует текущий корень из настроек приложения. Если требуется обработать журнал ранее выбранного корня после переключения настройки, укажите его явно, не меняя пользовательские настройки:

```powershell
$RecoveryRoot = 'D:\HLA_Laboratory_System'
HLA_Laboratory_System.exe --file-base-recovery list --root $RecoveryRoot
```

Явный путь проходит те же проверки профиля развёртывания, роли, манифеста и защиты, после чего команда получает ту же блокировку обслуживания. Параметр `--root` ставится последним; для выбранного решения добавьте `--root $RecoveryRoot` после UUID операции.

После сверки выберите ровно один вариант. Не выполняйте обе команды для одной операции.

Подтвердить уже зафиксированную операцию:

```powershell
$OperationUuid = '<UUID_ОПЕРАЦИИ>'
if ($OperationUuid -match '<[^>]+>') {
    throw 'Замените UUID операции в угловых скобках.'
}
HLA_Laboratory_System.exe --file-base-recovery accept-committed $OperationUuid
```

Либо откатить только файловые изменения вместо подтверждения:

```powershell
$OperationUuid = '<UUID_ОПЕРАЦИИ>'
if ($OperationUuid -match '<[^>]+>') {
    throw 'Замените UUID операции в угловых скобках.'
}
HLA_Laboratory_System.exe --file-base-recovery rollback-files $OperationUuid
```

Механизм журнала не добавляет служебных объектов в схему PostgreSQL, не выполняет автоматический откат БД и не угадывает результат фиксации. `accept-committed` разрешён только после ручного подтверждения зафиксированного результата PostgreSQL и файлов; `rollback-files` изменяет только файлы и разрешён лишь после ручного приведения PostgreSQL к состоянию до операции. В EFS-режиме администратор должен одновременно входить в разрешённую группу чтения и иметь действующий EFS-ключ получателя.

Список получателей существующего корня не изменяется на месте. Плановое добавление, удаление или ротация EFS-получателей выполняется в окно обслуживания подготовкой нового чистого физического корня, полным аудитом и контролируемым переключением. Не редактируйте `protection.json`, `.hla_internal` или DDF вручную.
