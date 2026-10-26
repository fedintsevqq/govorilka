# Homebrew: формула для своего «крана» github.com/fedintsevqq/homebrew-tap (файл Casks/govorilka.rb).
# Тогда установка на Mac:  brew install --cask fedintsevqq/tap/govorilka
cask "govorilka" do
  arch arm: "apple-silicon", intel: "intel"

  version "0.3.3"
  sha256 arm:   "5d6740e4383fa8748e18eea0c3323d91fcbc76a7f12c9fa0392bb3c9fae1d64f",
         intel: "22ef3f10aa91182221f1e9845661c3169422a787182a317b719f3150098666ad"

  url "https://github.com/fedintsevqq/govorilka/releases/download/v#{version}/Govorilka-#{version}-macos-#{arch}.dmg"
  name "Говорилка"
  name "Govorilka"
  desc "Offline meeting transcription and dictation for Russian speech"
  homepage "https://github.com/fedintsevqq/govorilka"

  livecheck do
    url :url
    strategy :github_latest
  end

  depends_on macos: :ventura

  app "Govorilka.app"

  # программа подписана своим сертификатом, а не Apple: без снятия карантина macOS не даст её открыть
  postflight do
    system_command "/usr/bin/xattr", args: ["-dr", "com.apple.quarantine", "#{appdir}/Govorilka.app"]
  end

  zap trash: [
    "~/Library/Application Support/Govorilka",
    "~/Library/LaunchAgents/io.github.fedintsevqq.govorilka.plist",
  ]
end
