// Switchboard Links.app (the link app): a stand-in "browser" that macOS hands every clicked link.
// (Before 0.2.0 it was named Switchboard.app. Its bundle identifier has not changed, so it stays the
// default browser wherever it was.)
//
// A claude.ai artifact link goes to Switchboard's page on this Mac, which opens it in the browser profile
// of the account that owns it. When the page doesn't answer within a second and a half (it is down,
// restarting or busy), the link opens from the owner map the page keeps (~/.switchboard/owners.json): in its
// owner's profile, never in whatever window was used last. Every other link goes straight to the browser
// that was the default before, exactly as it always did. If anything here fails, the link still opens.
//
//   SwitchboardLinks --status         print the app that is the default browser now
//   SwitchboardLinks --make-default   ask macOS to make Switchboard Links the default browser
//   SwitchboardLinks --restore        ask macOS to give the default back to the usual browser
//   SwitchboardLinks --route URL      print where a link would go ("owner" or "plain") and leave
//   SwitchboardLinks --owner URL      print the profile the owner map opens an artifact link in ("" when none)
//   SwitchboardLinks --decide URL     route a link as a click does, but print what this app did instead of
//                                     opening anything itself: "page", "open <profile>" or "plain"
//   SwitchboardLinks --page           print the page address an owner link is sent to, and leave
import Cocoa

let environment = ProcessInfo.processInfo.environment
/// Switchboard's folder, as its own code finds it: $SWITCHBOARD_HOME, else ~/.switchboard.
let dataDir = (environment["SWITCHBOARD_HOME"].flatMap { $0.isEmpty ? nil : $0 })
    ?? NSString(string: "~/.switchboard").expandingTildeInPath
let me = Bundle.main.bundleIdentifier ?? "io.github.amyjainberkeley.switchboard.links"
let chromeID = "com.google.Chrome"
/// How long the page has to take a link before this app opens it from the owner map itself. The page is told
/// (fallback_after), so a request that reaches it later than this is not opened a second time.
let pageWait = 1.5
let profileRule = "^[A-Za-z0-9 _-]{1,40}$"

/// One-line settings files written by the page: port, home-profile.txt, profile-browser.txt, previous-browser.txt.
func setting(_ name: String) -> String {
    ((try? String(contentsOfFile: dataDir + "/" + name, encoding: .utf8)) ?? "")
        .trimmingCharacters(in: .whitespacesAndNewlines)
}

/// The page's port: SWITCHBOARD_PORT, then the port the page wrote down when it started (`port`), then the one set in
/// settings.json (`switchboard update --port N`), then 7799. (switchboard/linkapp.py page_port: the same order.)
func pagePort() -> Int {
    var settingsPort = ""
    if let data = FileManager.default.contents(atPath: dataDir + "/settings.json"),
       let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
       let port = json["port"] as? NSNumber, CFGetTypeID(port) != CFBooleanGetTypeID() {
        settingsPort = port.stringValue
    }
    for said in [environment["SWITCHBOARD_PORT"] ?? "", setting("port"), settingsPort] {
        if let n = Int(said.trimmingCharacters(in: .whitespaces)), (1...65535).contains(n) { return n }
    }
    return 7799
}

func pageURL() -> URL {
    URL(string: "http://127.0.0.1:\(pagePort())/api/go")!
}

/// The page is on this Mac: it is asked directly, never through a proxy (a system proxy that does not list 127.0.0.1
/// would take the call elsewhere, and a running page would look stopped), as every other part of Switchboard asks it.
let direct: URLSession = {
    let config = URLSessionConfiguration.ephemeral
    config.connectionProxyDictionary = [:]
    return URLSession(configuration: config)
}()

/// The browser ordinary links go to: the one that was the default before, else Chrome, else Safari. Never this app
/// itself (a previous-browser.txt naming it would send links round in a circle).
func usualBrowser() -> URL? {
    for id in [setting("previous-browser.txt"), chromeID, "com.apple.Safari"] where !id.isEmpty && id != me {
        if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: id) { return url }
    }
    return nil
}

/// The browser that holds the Claude profiles (profile-browser.txt, else Chrome), if it is installed.
func holderBrowser() -> URL? {
    let holder = setting("profile-browser.txt").isEmpty ? chromeID : setting("profile-browser.txt")
    guard holder != me else { return nil }
    return NSWorkspace.shared.urlForApplication(withBundleIdentifier: holder)
}

func openPlain(_ url: URL) {
    guard let app = usualBrowser() else { return }
    let plain = NSWorkspace.OpenConfiguration()
    // "Other links open in" on the page: a profile of the browser that holds the Claude profiles.
    let home = setting("home-profile.txt")
    let safe = home.range(of: profileRule, options: .regularExpression) != nil
    let holder = setting("profile-browser.txt").isEmpty ? chromeID : setting("profile-browser.txt")
    guard safe, Bundle(url: app)?.bundleIdentifier == holder else {
        NSWorkspace.shared.open([url], withApplicationAt: app, configuration: plain)
        return
    }
    openInProfile(url, home, app)
}

/// Same as `open -na "Google Chrome" --args --profile-directory=... URL`: the short-lived second copy hands the link
/// to the browser that is already running, then leaves.
func openInProfile(_ url: URL, _ profile: String, _ app: URL) {
    let config = NSWorkspace.OpenConfiguration()
    config.createsNewApplicationInstance = true
    config.arguments = ["--profile-directory=" + profile, url.absoluteString]
    NSWorkspace.shared.openApplication(at: app, configuration: config) { _, error in
        if error != nil { NSWorkspace.shared.open([url], withApplicationAt: app, configuration: NSWorkspace.OpenConfiguration()) }
    }
}

/// The link inside a well-known redirect (Outlook's safe links, Google's /url, Slack's redirect), or nil.
func unwrapped(_ c: URLComponents) -> String? {
    let host = c.host?.lowercased() ?? ""
    var names: [String] = []
    if host.hasSuffix(".safelinks.protection.outlook.com") { names = ["url"] }
    else if (host == "www.google.com" || host == "google.com") && c.path == "/url" { names = ["q", "url"] }
    else if host == "slack-redir.net" && c.path == "/link" { names = ["url"] }
    for n in names {
        if let v = c.queryItems?.first(where: { $0.name == n })?.value, !v.isEmpty { return v }
    }
    return nil
}

/// A claude.ai artifact link written plainly ("https://claude.ai/..."), or nil: https only, the host claude.ai itself
/// (any case; never claude.ai.example, never a login part like claude.ai@example), port 443 or none, and a path that
/// starts /artifact/<id> or /code/artifact/<id>. (switchboard/artifacts.py parse_link: the same rule.)
func artifactLink(_ url: URL, depth: Int = 0) -> URL? {
    guard let c = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
    if c.user != nil || c.password != nil { return nil }
    if depth < 2, let inner = unwrapped(c), let innerURL = URL(string: inner) { return artifactLink(innerURL, depth: depth + 1) }
    guard c.scheme?.lowercased() == "https", c.host?.lowercased() == "claude.ai", c.port == nil || c.port == 443
    else { return nil }
    let path = c.percentEncodedPath
    guard path.range(of: "^/(code/)?artifact/[A-Za-z0-9_-]{16,}(/|$)", options: .regularExpression) != nil else { return nil }
    var out = "https://claude.ai" + path
    if let q = c.percentEncodedQuery { out += "?" + q }
    if let f = c.percentEncodedFragment { out += "#" + f }
    return URL(string: out)
}

func isArtifact(_ url: URL) -> Bool { artifactLink(url) != nil }

/// The artifact's id: the path part after /artifact/.
func artifactID(_ url: URL) -> String? {
    let parts = url.path.split(separator: "/").map(String.init)
    guard let i = parts.firstIndex(of: "artifact"), i + 1 < parts.count else { return nil }
    return parts[i + 1]
}

/// Where the owner map opens an artifact: its owner's profile, else the profile it loaded in, else (only when its
/// owner is not known) the profile the map keeps for links nobody knows yet. nil when none (or no map).
/// (switchboard/artifacts.py map_lookup: the same rule.)
func ownerProfile(_ url: URL) -> String? {
    guard let id = artifactID(url),
          let data = FileManager.default.contents(atPath: dataDir + "/owners.json"),
          let map = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          (map["v"] as? NSNumber)?.intValue == 1 else { return nil }
    let item = (map["artifacts"] as? [String: Any])?[id] as? [String: Any]
    let owner = item?["o"] as? String
    var profile = item?["p"] as? String
    if profile == nil && owner == nil { profile = map["fallback"] as? String }
    guard let p = profile, p.range(of: profileRule, options: .regularExpression) != nil else { return nil }
    return p
}

/// The page didn't take it: open it from the owner map, in its owner's profile, else the plain way.
func openFromMap(_ link: URL, _ original: URL) {
    if let profile = ownerProfile(link), let app = holderBrowser() {
        openInProfile(link, profile, app)
    } else {
        openPlain(original)
    }
}

/// Hand an artifact link to the page. done(true) when the page took it (it opens it, checks the tab and learns).
func askPage(_ link: URL, _ done: @escaping (Bool) -> Void) {
    // The request's own timeout only frees the connection; whether the page took the link is decided by the timer
    // below, at pageWait.
    var request = URLRequest(url: pageURL(), timeoutInterval: 2.5)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("1", forHTTPHeaderField: "X-Switchboard")
    let body: [String: Any] = ["url": link.absoluteString, "wait": false,
                               "sent_at": Date().timeIntervalSince1970, "fallback_after": pageWait]
    request.httpBody = try? JSONSerialization.data(withJSONObject: body)
    var answered = false
    let lock = NSLock()
    func once(_ ok: Bool) {
        lock.lock(); defer { lock.unlock() }
        if answered { return }
        answered = true
        done(ok)
    }
    direct.dataTask(with: request) { _, response, _ in
        once((response as? HTTPURLResponse)?.statusCode == 200)
    }.resume()
    // A page that accepted the connection but never answers still loses the link after pageWait (+0.2 s: the page
    // opens a request that reaches it by then and not one later, artifacts.POSTER_SLACK).
    DispatchQueue.global().asyncAfter(deadline: .now() + pageWait + 0.2) { once(false) }
}

func route(_ url: URL) {
    guard let link = artifactLink(url) else { return openPlain(url) }
    askPage(link) { ok in
        if !ok { DispatchQueue.main.async { openFromMap(link, url) } }   // the page is down or slow: the owner map
    }
}

func currentDefault() -> URL? {
    NSWorkspace.shared.urlForApplication(toOpen: URL(string: "https://example.com")!)
}

func setDefault(_ app: URL) {
    // macOS shows its own "change your default web browser?" question. Only a person can answer it.
    NSWorkspace.shared.setDefaultApplication(at: app, toOpenURLsWithScheme: "http") { error in
        if let error = error { print("not changed: \(error.localizedDescription)"); exit(1) }
        NSWorkspace.shared.setDefaultApplication(at: app, toOpenURLsWithScheme: "https") { _ in
            print("default browser: \(app.path)"); exit(0)
        }
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 180) { print("no answer"); exit(2) }
    RunLoop.main.run()
}

final class Delegate: NSObject, NSApplicationDelegate {
    var quit: DispatchWorkItem?

    func application(_ application: NSApplication, open urls: [URL]) {
        urls.forEach(route)
        leaveSoon()
    }
    func applicationDidFinishLaunching(_ notification: Notification) { leaveSoon() }

    // Nothing to keep running between clicks: step out a few seconds after the last link.
    func leaveSoon() {
        quit?.cancel()
        let work = DispatchWorkItem { NSApp.terminate(nil) }
        quit = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 6, execute: work)
    }
}

let args = CommandLine.arguments
func argument(after flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    return args[i + 1]
}

if args.contains("--status") {
    print(currentDefault()?.path ?? "")
    exit(0)
} else if let given = argument(after: "--route") {
    print(URL(string: given).map(isArtifact) == true ? "owner" : "plain")
    exit(0)
} else if let given = argument(after: "--owner") {
    print(URL(string: given).flatMap { artifactLink($0) }.flatMap(ownerProfile) ?? "")
    exit(0)
} else if let given = argument(after: "--decide") {
    guard let url = URL(string: given), let link = artifactLink(url) else { print("plain"); exit(0) }
    let wait = DispatchSemaphore(value: 0)
    var took = false
    askPage(link) { ok in took = ok; wait.signal() }
    wait.wait()
    if took { print("page") } else if let p = ownerProfile(link) { print("open \(p)") } else { print("plain") }
    exit(0)
} else if args.contains("--page") {
    print(pageURL().absoluteString)
    exit(0)
} else if args.contains("--make-default") {
    // Remember who had the seat, so ordinary links keep going there and --restore can hand it back.
    if let now = currentDefault(), let id = Bundle(url: now)?.bundleIdentifier, id != me {
        try? FileManager.default.createDirectory(atPath: dataDir, withIntermediateDirectories: true)
        try? id.write(toFile: dataDir + "/previous-browser.txt", atomically: true, encoding: .utf8)
    }
    setDefault(Bundle.main.bundleURL)
} else if args.contains("--restore") {
    guard let usual = usualBrowser() else { print("no other browser found"); exit(1) }
    setDefault(usual)
} else {
    let app = NSApplication.shared
    let delegate = Delegate()
    app.delegate = delegate
    app.run()
}
