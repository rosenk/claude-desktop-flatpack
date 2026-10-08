# Claude Desktop — unofficial Flatpak

Автоматично препакетиране на **официалния Linux `.deb` на Anthropic** във
Flatpak за **x86_64 и ARM64 (aarch64)**. Не използва Windows инсталатора и не
променя `app.asar`. Това не е официален пакет и не е свързан с Anthropic.

Официалният Linux клиент е beta и се разпространява като `.deb` и през APT:
[Claude Desktop on Linux](https://code.claude.com/docs/en/desktop-linux).

## Как работи

GitHub Actions проверява APT хранилището на Anthropic на всеки 6 часа
(`00:23`, `06:23`, `12:23`, `18:23` UTC; GitHub може да забавя cron задачите).

1. Проверява фиксирания fingerprint на ключа на Anthropic, подписа на
   `InRelease`, датата и `Valid-Until`, както и SHA-256 и размера на двата
   `Packages` индекса.
2. Избира най-новия пакет за всяка архитектура с Debian version ordering,
   а не с текстово сортиране. Генерира manifests с конкретен URL и SHA-256.
3. Сравнява версията, URL и checksum с **действително публикувания**
   `versions.json` в GitHub Pages. Без промяна няма build или deployment.
4. Пакетира на native GitHub runners: `ubuntu-24.04` и `ubuntu-24.04-arm`.
   Проверява зависимостите в Platform runtime, изпълнява `--version` и OSTree fsck.
5. Обединява двата build-а в OSTree repo, подписва app commits и repo metadata
   с постоянен GPG ключ и публикува целия сайт с GitHub Pages deployment.

И двете архитектури трябва да минат успешно преди публикуване. Неуспешен
build/sign/deploy не се записва като публикувана версия; следващата cron
проверка опитва отново. Concurrency предотвратява застъпващи се deployments.

Използва се GitHub Pages, а не Git branch за големите бинарни файлове.
Публикува се само текущото състояние на repo-то — без история и static deltas,
с проверка за размер под 950 MB, за да остане под Pages лимита от 1 GB.
Вече инсталираните клиенти могат да обновяват с `flatpak update`, но това repo
не предлага връщане към предишни версии. При превишаване на лимита задачата
спира и е нужен друг хостинг. Спазвай и лимитите на GitHub Pages за трафик.

## Еднократно активиране от собственика

Кодът сам по себе си не активира Pages и не създава signing secret.
Преди първия deployment:

1. Публикувай кода в `main` на `rosenk/claude-desktop-flatpack`. За безплатни
   публични ARM64 runners и Pages използвай публично хранилище.
2. В **Settings → Pages → Build and deployment → Source** избери
   **GitHub Actions**. При custom Pages domain трябва да промениш
   `PAGES_URL` и `REPO_URL` в `.github/workflows/flatpak.yml`.
3. Създай отделен GPG signing ключ и го запази. Не генерирай нов ключ при
   всяко изпълнение: инсталираните клиенти се доверяват на първия ключ.
4. Задай repository secret `FLATPAK_GPG_PRIVATE_KEY` с ASCII-armored private
   key и repository variable `FLATPAK_GPG_KEY_ID` с **пълния fingerprint**.
5. В **Actions → Monitor, build and publish Flatpak → Run workflow** пусни
   първата задача с `force=true`. Последващите проверки са автоматични.

Пример с `gpg` и GitHub CLI, който изпълняваш на доверената си машина
(private key не се показва в терминала и не се записва в Git):

```bash
export GNUPGHOME="$(mktemp -d)"
chmod 700 "$GNUPGHOME"
gpg --batch --pinentry-mode loopback --passphrase '' \
  --quick-generate-key 'Claude Flatpak signing <flatpak@users.noreply.github.com>' rsa3072 sign 0
KEY_ID=$(gpg --batch --with-colons --list-secret-keys | awk -F: '$1 == "fpr" {print $10; exit}')
gpg --batch --armor --export-secret-keys "$KEY_ID" |
  gh secret set FLATPAK_GPG_PRIVATE_KEY --repo rosenk/claude-desktop-flatpack
gh variable set FLATPAK_GPG_KEY_ID --body "$KEY_ID" --repo rosenk/claude-desktop-flatpack
gpg --batch --armor --export "$KEY_ID" > claude-flatpak-public-key.asc
printf 'Signing fingerprint: %s\nSecurely back up GPG home: %s\n' "$KEY_ID" "$GNUPGHOME"
```

Този CI ключ е без passphrase, за да подписва неинтерактивно. Ограничаването
на достъпа до GitHub secret и защитата на `main` са важни. Направи сигурен
backup на ключа извън хранилището; не изтривай временния `GNUPGHOME`, преди
да го архивираш безопасно. Public key и fingerprint могат да се публикуват.
Прегледай условията на Anthropic и правото за публично преразпространение
на proprietary бинарните файлове преди активиране; този проект не предоставя
лиценз върху самото приложение.

GitHub може да спре scheduled workflows след 60 дни без активност в публично
repo. Следи failed run известията и при нужда активирай отново workflow-а.

## Инсталиране след първото успешно публикуване

Следният URL **няма да работи преди активиране и успешен deployment**:

```bash
flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak remote-add --user --if-not-exists claude-desktop \
  https://rosenk.github.io/claude-desktop-flatpack/claude-desktop.flatpakrepo
flatpak install --user claude-desktop io.github.rosenk.ClaudeDesktop
flatpak run io.github.rosenk.ClaudeDesktop
```

Flatpak избира архитектурата автоматично. Обновяване:

```bash
flatpak update --user io.github.rosenk.ClaudeDesktop
```

Не се използва `--no-gpg-verify`: `.flatpakrepo` съдържа публичния signing key.
Първоначалното доверие идва от HTTPS Pages URL; можеш отделно да провериш
fingerprint-а на публикувания `repo-key.gpg` с `gpg --show-keys`.

## Sandbox и ограничения

- Използва Freedesktop Platform **25.08**, X11 (XWayland на Wayland desktops),
  мрежа, звук, GPU, Secret Service и tray D-Bus достъп. Няма общ достъп до
  home/host файловата система, host команди или всички устройства.
- Chromium се стартира с `--no-sandbox`, защото setuid/nested Chromium
  sandbox не работи в този Flatpak. **Външният Flatpak sandbox остава**, но
  изолацията между Chromium процесите е намалена. Не приемай това като
  еквивалентна сигурност на официалната `.deb` инсталация.
- Конфигурацията е отделна: обичайно
  `~/.var/app/io.github.rosenk.ClaudeDesktop/config/Claude/`.
  Съществуващата `.deb` конфигурация не се мигрира автоматично.
- **Cowork не се поддържа от тази опаковка**: нужни са KVM/QEMU/virtiofsd и
  допълнителна интеграция. Не са включени и не се дава `/dev/kvm` достъп.
- **Code и MCP не са гарантирани**: host инструменти, Node/Python, shell
  команди и произволни project директории не са автоматично достъпни.
  Входът, file portals, tray и глобалните shortcuts изискват проверка на
  реалния desktop; `--version` smoke test не доказва тези функции.
- При нужда от конкретна project директория можеш изрично да я разрешиш:

  ```bash
  flatpak override --user --filesystem="$HOME/Projects" io.github.rosenk.ClaudeDesktop
  ```

  Това разширява достъпа до файлове, но не добавя host executables или Cowork.

## Локална проверка и build

Нужни са Linux, Python 3, `dpkg`, `gpg`/`gpgv`, Flatpak и **flatpak-builder
1.4+** (старите 1.2.x не могат да compose-нат AppStream с SDK 25.08).

```bash
python3 -m unittest discover -s tests -v
bash tests/test_publish.sh
bash -n scripts/publish.sh
sh -n packaging/claude-desktop
desktop-file-validate packaging/*.desktop
python3 scripts/upstream.py --force
flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub org.freedesktop.Platform//25.08 org.freedesktop.Sdk//25.08
flatpak-builder --user --force-clean --disable-rofiles-fuse --repo=repo \
  --default-branch=stable build generated/$(flatpak --default-arch).json
flatpak build --runtime build /app/bin/claude-desktop --version
```

`scripts/publish.sh` подготвя само локалния `dist/`: очаква двата build repos
в `repos/x86_64` и `repos/aarch64`, `generated/versions.json`, както и
`GNUPGHOME`, `FLATPAK_GPG_KEY_ID` и `REPO_URL`. Самият deployment се прави
само от Actions. При промяна на packaging файловете push към `main` прави
rebuild дори версията на Anthropic да е същата.
