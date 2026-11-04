import SwiftUI
import WebKit
import AppKit

struct Offer: Codable, Identifiable, Equatable {
    var id = UUID().uuidString
    var url = ""
    var server = ""
    var level = ""
    var championCount = ""
    var skinCount = ""
    var essence = ""
    var rank = ""
    var previousRank = "Unranked"
    var price = ""
    var champions = ""
    var skins = ""
    var featured = ""
    var opgg = ""
    var mail = "Не определено"
    var raw = ""
    var titleOverride = ""
    var descriptionOverride = ""
    var updated = Date()
    var names: [String] { skins.components(separatedBy: CharacterSet(charactersIn: ",\n")).map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty } }
    var title: String {
        let n = Double(essence.replacingOccurrences(of: " ", with: ""))
        let k = n.map { String(format: "%g", floor($0 / 1000)) + "К" } ?? "[СЭ]"
        return "🔶【\(featured.isEmpty ? "[выберите скины]" : featured)】🔶 【\(skinCount.isEmpty ? "?" : skinCount) СКИНОВ | \(championCount.isEmpty ? "?" : championCount) ЧЕМПИОНОВ \(k) СЭ】🎮🟠【АВТОВЫДАЧА】🟠"
    }
    var description: String {
        let auto = mail == "Авторег, почта подтверждена" || mail == "Доступ к почте передаётся"
        return """
💪 Аккаунт личный! был прокачан вручную
⚙️ Аккаунт полностью переходит в ваше владение. вы являетесь ЕДИНСТВЕННЫМ владельцем.
\(auto ? "🔒Вы получаете данные в формате почта / пароль от аккаунта / пароль от почты, почту можно сменить" : "🔒 На данном аккаунте не привязана почта, вы можете привязать свою почту и сменить все данные")
\(auto ? "✅ гарантия на аккаунт 30 дней" : "✅ Пожизненная гарантия на аккаунт")

📘 Синяя Эссенция: \(essence.isEmpty ? "[не указано]" : essence)
🎖️ Ранг в этом сезоне: \(rank.isEmpty ? "[не указано]" : rank)
🎖️ Ранг в предыдущем сезоне: Unranked

Чемпионы(\(championCount)): \(champions)

Скины(\(skinCount)): \(skins)

✍️ Статистика: \(opgg.isEmpty ? "[ссылка не найдена]" : opgg)
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
📩Если есть какие-то вопросы — спрашивайте 💬
📩 Обязательно отвечу и помогу 💬
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
📦 На все аккаунты включена автовыдача. Даже если меня нет на месте, данные от аккаунта придут вам сразу же после оплаты.
❗️Возврат средств не осуществляется, если вы передумали, купили не тот аккаунт или решили, что он вам не нужен. Покупая аккаунт, вы соглашаетесь с условиями магазина. Внимательно прочитайте всё перед покупкой ❗️
"""
    }
    var missing: [String] {
        [("Сервер",server),("Уровень",level),("Количество чемпионов",championCount),("Количество скинов",skinCount),("Синяя эссенция",essence),("Закупочная цена",price),("Текущий ранг",rank),("Чемпионы",champions),("Скины",skins),("Скины для заголовка",featured),("OP.GG",opgg)].filter{$0.1.isEmpty}.map{$0.0} + (mail == "Не определено" ? ["Статус почты"] : [])
    }
}

struct Parser {
    static func match(_ pattern: String, _ text: String) -> String? {
        guard let r = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]), let m = r.firstMatch(in:text,range:NSRange(text.startIndex...,in:text)), let range = Range(m.range(at:1),in:text) else { return nil }
        return String(text[range]).trimmingCharacters(in:.whitespacesAndNewlines)
    }
    static func opggLink(_ candidates: [String]) -> String? {
        var queue = candidates
        var seen = Set<String>()
        var index = 0
        while index < queue.count && index < 3000 {
            let raw = queue[index]; index += 1
            guard seen.insert(raw).inserted else { continue }
            let value = raw.replacingOccurrences(of:"&amp;",with:"&")
                .replacingOccurrences(of:"\\/",with:"/")
            if let decoded=value.removingPercentEncoding, decoded != value {queue.append(decoded)}
            // LZT may wrap the external destination in its redirect URL.
            if let parts=URLComponents(string:value), let queries=parts.queryItems {
                queue.append(contentsOf:queries.compactMap(\.value))
            }
            let base64=value.replacingOccurrences(of:"-",with:"+").replacingOccurrences(of:"_",with:"/")
            if base64.count > 12, let data=Data(base64Encoded:base64 + String(repeating:"=",count:(4-base64.count%4)%4)), let decoded=String(data:data,encoding:.utf8), decoded.contains("op.gg") || decoded.contains("op%2Egg") {queue.append(decoded)}
            if let found=match(#"(?<![a-z0-9.-])((?:https?://|//)?(?:[a-z0-9-]+\.)*op\.gg/[^\s<>"'\)\]]+)"#,value) {
                let normalized=found.hasPrefix("//") ? "https:" + found : (found.lowercased().hasPrefix("http") ? found : "https://" + found)
                if let url=URL(string:normalized), let host=url.host?.lowercased(), host == "op.gg" || host.hasSuffix(".op.gg"), ["https","http"].contains(url.scheme ?? "") {return url.absoluteString}
            }
        }
        return nil
    }
    static func parse(_ text: String, into input: Offer, links: [String] = []) -> Offer {
        var o = input
        o.raw = text
        let num = "([0-9][0-9 \\u00a0]*)"
        func value(_ labels: String) -> String? { match("(?:" + labels + ")[ \\t:：]*\\n?[ \\t]*([^\\n]+)",text) }
        func number(_ labels: String) -> String? { match("(?:" + labels + ")[ \\t:：()]*\\n?[ \\t]*" + num,text)?.filter { $0.isNumber } }
        // The marketplace renders stat values BEFORE their labels. Limit searches
        // to account information so seller reviews never become offer data.
        let lines = text.components(separatedBy:.newlines).map{$0.trimmingCharacters(in:.whitespacesAndNewlines)}.filter{!$0.isEmpty}
        let accountStart = lines.firstIndex(of:"League of Legends")
        let stats = accountStart.map { Array(lines[$0...]) } ?? lines
        func before(_ labels:[String]) -> String? {
            guard let i=stats.firstIndex(where:{labels.contains($0)}), i > 0 else {return nil}
            return stats[i-1]
        }
        let prefix = text.components(separatedBy:"Гарантия").first ?? text
        if let v = before(["Регион","Region"]) ?? value("Сервер|Server") {o.server=v}
        if let v = before(["Уровень","Level"]) ?? number("Уровень(?: аккаунта)?|Level") {o.level=v}
        if let v = before(["Blue essence","Синяя эссенция"]) ?? number("Синяя [эЭ]ссенция|Blue essence") {o.essence=v}
        if let v = before(["Ранг","Rank"]) ?? value("Ранг в этом сезоне|Текущий ранг|Current rank") {o.rank=v}
        o.previousRank="Unranked"
        if let v = match("(?:Цена|Price)[ \t:]*([0-9][0-9 \u{00a0}.,]*[ \t]*(?:₽|руб\\.?|RUB|USD|EUR|\\$|€))",prefix) {o.price=v}
        if let v = opggLink(links + [text]) {o.opgg=v}
        let low = prefix.lowercased()
        if low.contains("не подтверждена почта") || low.contains("почта не подтверждена") {o.mail="Почта не подтверждена"}
        else if low.replacingOccurrences(of:"\u{00a0}",with:" ").split(whereSeparator:{$0.isWhitespace}).joined(separator:" ").contains("вы получите доступ к почте от аккаунта") {o.mail="Доступ к почте передаётся"}
        else if low.contains("авторег") {o.mail="Авторег, почта подтверждена"}
        func section(start:(String)->Bool,end:(String)->Bool) -> [String]? {
            guard let i=lines.firstIndex(where:start), i+1 < lines.count else {return nil}
            let rest=Array(lines[(i+1)...]); guard let j=rest.firstIndex(where:end) else {return nil}
            return Array(rest[..<j])
        }
        if let items=section(start:{$0 == "Персонажи" || $0 == "Чемпионы" || $0 == "Champions"},end:{$0.range(of:"^(?:[0-9]+ скин|Скины|Skins)",options:.regularExpression) != nil}), !items.isEmpty {
            o.champions=items.joined(separator:", ");o.championCount=String(items.count)
        } else if let v=number("Количество чемпионов|Чемпионы|Champions") {o.championCount=v}
        if let items=section(start:{$0.range(of:"^(?:[0-9]+ скин|Скины|Skins)",options:.regularExpression) != nil},end:{$0 == "League of Legends" || $0.hasPrefix("Достоверная информация")}), !items.isEmpty {
            o.skins=items.joined(separator:", ")
            o.skinCount=match("(?:^|\\n)([0-9]+) скин",text) ?? String(items.count)
        } else if let v=number("Количество скинов|Скины|Skins") {o.skinCount=v}
        if o.featured.isEmpty {
            let rare=["Вершительница Кейл","Серый Варвик","Средневековый Твич","Judgment Kayle","Grey Warwick","Medieval Twitch"]
            let priority=o.names.filter{rare.contains($0)}
            o.featured=Array((priority + o.names.filter{!priority.contains($0)}).prefix(3)).joined(separator:", ")
        }
        return o
    }
}

@MainActor final class Store: NSObject, ObservableObject, WKNavigationDelegate {
    @Published var offers: [Offer] = []
    @Published var current = Offer()
    @Published var status = "Откройте оффер и считайте страницу"
    @Published var address = ""
    @Published var busy = false
    let web = WKWebView(frame:.zero, configuration:WKWebViewConfiguration())
    let folder = FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("OfferStudio/Offers")
    override init() {
        super.init()
        web.navigationDelegate = self
        web.customUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
        do {
            try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
            offers = try FileManager.default.contentsOfDirectory(at:folder,includingPropertiesForKeys:nil).filter{$0.pathExtension == "json"}.compactMap{try? JSONDecoder().decode(Offer.self,from:Data(contentsOf:$0))}.sorted{$0.updated > $1.updated}
            for i in offers.indices {
                offers[i].previousRank="Unranked"
                offers[i].descriptionOverride=offers[i].descriptionOverride.replacingOccurrences(of:"(?m)^🎖️ Ранг в предыдущем сезоне:.*$",with:"🎖️ Ранг в предыдущем сезоне: Unranked",options:.regularExpression)
                let raw = offers[i].raw.components(separatedBy:"Гарантия").first ?? ""
                let normalized = raw.lowercased().split(whereSeparator:{$0.isWhitespace}).joined(separator:" ")
                if normalized.contains("вы получите доступ к почте от аккаунта"), !normalized.contains("не подтверждена почта"), !normalized.contains("почта не подтверждена") {
                    offers[i].mail = "Доступ к почте передаётся"
                    offers[i].descriptionOverride = offers[i].descriptionOverride
                        .replacingOccurrences(of:"🔒 На данном аккаунте не привязана почта, вы можете привязать свою почту и сменить все данные",with:"🔒Вы получаете данные в формате почта / пароль от аккаунта / пароль от почты, почту можно сменить")
                        .replacingOccurrences(of:"✅ Пожизненная гарантия на аккаунт",with:"✅ гарантия на аккаунт 30 дней")
                    try JSONEncoder().encode(offers[i]).write(to:folder.appendingPathComponent(offers[i].id + ".json"),options:.atomic)
                }
            }
        } catch { status = "Ошибка чтения архива: \(error.localizedDescription)" }
    }
    nonisolated func webView(_ webView:WKWebView, didFinish navigation:WKNavigation!) {
        Task { @MainActor in self.status = "Страница загружена. Нажмите «Считать страницу»" }
    }
    nonisolated func webView(_ webView:WKWebView, didFailProvisionalNavigation navigation:WKNavigation!, withError error:Error) {
        let failure = error as NSError
        guard !(failure.domain == NSURLErrorDomain && failure.code == NSURLErrorCancelled) else { return }
        Task { @MainActor in self.status = "Ошибка загрузки: \(error.localizedDescription). Можно вставить текст из обычного браузера." }
    }
    nonisolated func webView(_ webView:WKWebView, didFail navigation:WKNavigation!, withError error:Error) {
        let failure = error as NSError
        guard !(failure.domain == NSURLErrorDomain && failure.code == NSURLErrorCancelled) else { return }
        Task { @MainActor in self.status = "Ошибка страницы: \(error.localizedDescription)" }
    }
    func save() {
        current.updated = Date()
        do {
            let data = try JSONEncoder().encode(current)
            try data.write(to:folder.appendingPathComponent(current.id + ".json"),options:.atomic)
            if let i = offers.firstIndex(where:{$0.id == current.id}) { offers[i] = current } else { offers.insert(current,at:0) }
            status = "Сохранено · \(Date().formatted(date:.omitted,time:.shortened))"
        } catch { status = "Не удалось сохранить: \(error.localizedDescription)" }
    }
    func archive(_ offer:Offer) {
        pendingSave?.cancel()
        do {
            let archived=folder.deletingLastPathComponent().appendingPathComponent("Archived")
            try FileManager.default.createDirectory(at:archived,withIntermediateDirectories:true)
            let file=folder.appendingPathComponent(offer.id+".json")
            if FileManager.default.fileExists(atPath:file.path) {
                try FileManager.default.moveItem(at:file,to:archived.appendingPathComponent(offer.id+"-"+UUID().uuidString+".json"))
            }
            offers.removeAll{$0.id==offer.id}
            if current.id==offer.id {current=Offer();address=""}
            status="Убрано из списка. Сохранение перенесено в архив."
        } catch {status="Не удалось убрать оффер: "+error.localizedDescription}
    }
    private var navigationGeneration = UUID()
    func loadPage(_ url: URL, repair: Bool = false) {
        let generation = UUID()
        navigationGeneration = generation
        web.stopLoading()
        status = repair ? "Восстанавливаю загрузку сайта…" : "Подготовка страницы…"
        let types: Set<String> = repair
            ? [WKWebsiteDataTypeServiceWorkerRegistrations, WKWebsiteDataTypeDiskCache, WKWebsiteDataTypeMemoryCache, WKWebsiteDataTypeFetchCache]
            : [WKWebsiteDataTypeServiceWorkerRegistrations]
        web.configuration.websiteDataStore.fetchDataRecords(ofTypes: types) { [weak self] records in
            guard let self = self else { return }
            let site = records.filter { $0.displayName == "lzt.market" || $0.displayName.hasSuffix(".lzt.market") }
            self.web.configuration.websiteDataStore.removeData(ofTypes: types, for: site) { [weak self] in
                guard let self = self, self.navigationGeneration == generation else { return }
                self.web.load(URLRequest(url:url, cachePolicy:.reloadIgnoringLocalCacheData, timeoutInterval:30))
                self.status = "Загружаю сайт…"
            }
        }
    }
    func reloadPage() {
        guard let url = URL(string:current.url), url.host == "lzt.market" else { open(); return }
        loadPage(url, repair:true)
    }
    nonisolated func webViewWebContentProcessDidTerminate(_ webView:WKWebView) {
        Task { @MainActor in self.status = "Браузер остановился. Нажмите «Восстановить загрузку»." }
    }
    func open() {
        guard let u = URL(string:address), u.scheme == "https", ["lzt.market","www.lzt.market"].contains(u.host ?? ""), let id = u.pathComponents.first(where: { !$0.isEmpty && $0.allSatisfy(\.isNumber) }) else { status = "Нужна ссылка https://lzt.market/123456789/"; return }
        let canonical = "https://lzt.market/\(id)/"
        saveIfNeeded()
        if let old = offers.first(where:{$0.url == canonical}) { current = old } else { current = Offer(); current.url = canonical }
        loadPage(URL(string:canonical)!, repair:true)
    }
    private var pendingSave: Task<Void,Never>?
    func scheduleSave() {
        pendingSave?.cancel()
        let snapshot = current
        guard !snapshot.url.isEmpty || !snapshot.raw.isEmpty else { return }
        pendingSave = Task { [weak self] in
            try? await Task.sleep(nanoseconds:700_000_000)
            guard !Task.isCancelled, let self = self, self.current == snapshot else { return }
            self.save()
        }
    }
    func saveIfNeeded() { if !current.url.isEmpty || !current.raw.isEmpty { save() } }
    func read() {
        guard let u = web.url, ["lzt.market","www.lzt.market"].contains(u.host ?? ""), u.path == URL(string:current.url)?.path else { status = "Откройте страницу текущего оффера перед считыванием"; return }
        busy = true
        // The OP.GG control can be a button with data-href/onclick, or a
        // marketplace redirect rather than an ordinary direct anchor.
        let extraction = #"""
        (() => {
            const anchors = Array.from(document.querySelectorAll('a[href]')).map(a => a.href);
            const controls = Array.from(document.querySelectorAll('a,button,[role="button"],[data-href],[data-url],[onclick]'));
            const attributes = [];
            for (const control of controls) {
                const values = Array.from(control.attributes).filter(a => /href|url|link|onclick|data-/i.test(a.name)).map(a => a.value);
                if (/op\s*\.?\s*gg/i.test(control.textContent || '') || values.some(v => /op\.gg|op%2egg/i.test(v))) {
                    attributes.push(...values);
                    // Some controls put the destination on a nested icon/span.
                    for (const child of control.querySelectorAll('*')) {
                        attributes.push(...Array.from(child.attributes).filter(a => /href|url|link|onclick|data-/i.test(a.name)).map(a => a.value));
                    }
                }
            }
            return JSON.stringify({text:document.body.innerText,title:document.title,links:[...attributes,...anchors]});
        })()
        """#
        web.evaluateJavaScript(extraction) { [weak self] result,error in
            guard let self = self else {return}; self.busy = false
            if let error = error { self.status = "Ошибка чтения: \(error.localizedDescription)"; return }
            guard let s = result as? String, let data = s.data(using:.utf8), let obj = try? JSONSerialization.jsonObject(with:data) as? [String:Any], let text = obj["text"] as? String, text.count > 150 else { self.status = "Страница ещё не загрузилась. Пройдите проверку сайта и повторите."; return }
            var clean = Offer()
            clean.id = self.current.id; clean.url = self.current.url
            self.current = Parser.parse(text,into:clean,links:obj["links"] as? [String] ?? [])
            if let title=obj["title"] as? String, let price=Parser.match("(?:Цена|Price) ([0-9][0-9 \u{00a0}.,]*[₽$€])",title) {self.current.price=price}
            self.save()
            self.status = "Страница считана. Проверьте распознавание; не найдены: \(self.current.missing.joined(separator:", "))"
        }
    }
    func copy(_ text:String) { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text,forType:.string); status = "Скопировано" }
}

struct Browser: NSViewRepresentable {
    let web: WKWebView
    func makeNSView(context:Context) -> WKWebView {web}
    func updateNSView(_ nsView:WKWebView,context:Context) {}
}

struct ContentView: View {
    @StateObject var store = Store()
    @StateObject var sync = ServerSync()
    @State var tab = 0
    @State var copyWarning = false
    @State var pendingCopy = ""
    func field(_ label:String,_ key:WritableKeyPath<Offer,String>) -> some View {
        VStack(alignment:.leading,spacing:5) { Text(label).font(.caption).foregroundStyle(.secondary); TextField("Не указано",text:Binding(get:{store.current[keyPath:key]},set:{store.current[keyPath:key]=$0})).textFieldStyle(.roundedBorder) }
    }
    func editor(_ label:String,_ key:WritableKeyPath<Offer,String>,height:CGFloat=110) -> some View {
        VStack(alignment:.leading) { Text(label).font(.headline); TextEditor(text:Binding(get:{store.current[keyPath:key]},set:{store.current[keyPath:key]=$0})).font(.system(size:13)).frame(height:height).padding(6).background(.black.opacity(0.15)).clipShape(RoundedRectangle(cornerRadius:8)) }
    }
    func openOffer() {
        store.open()
        let url=store.current.url
        Task {await sync.restoreForAdding(url)}
    }
    func copyChecked(_ text:String) { if store.current.missing.isEmpty {store.copy(text)} else {pendingCopy=text;copyWarning=true} }
    var body: some View {
        HSplitView {
            VStack(alignment:.leading,spacing:16) {
                Label("Offer Studio",systemImage:"square.stack.3d.up.fill").font(.title2.bold())
                Text("Локальная библиотека офферов").font(.caption).foregroundStyle(.secondary)
                Button("Новый оффер",systemImage:"plus") {store.saveIfNeeded();store.current=Offer();store.tabReset()}
                ScrollView { VStack(spacing:8) { ForEach(store.offers) { offer in
                    Button {store.saveIfNeeded();store.current=offer;store.address=offer.url} label: {
                        VStack(alignment:.leading,spacing:5) { Text(offer.url.isEmpty ? "Черновик" : offer.url.replacingOccurrences(of:"https://lzt.market/",with:"#")).font(.headline); Text("\(offer.server) · \(offer.skinCount) скинов").font(.caption); Text(offer.price.isEmpty ? "Цена не указана" : offer.price).foregroundStyle(.orange); Text(sync.item(offer.url)?.statusText ?? "Локально · не отслеживается").font(.caption2).foregroundStyle(.secondary) }.frame(maxWidth:.infinity,alignment:.leading).padding(10).background(store.current.id == offer.id ? Color.accentColor.opacity(0.25) : Color.white.opacity(0.04)).clipShape(RoundedRectangle(cornerRadius:8))
                    }.buttonStyle(.plain)
                    .contextMenu {
                        Button("Удалить из списка и мониторинга",systemImage:"trash") {
                            Task {
                                if await sync.queueArchive(offer.url) {
                                    store.archive(offer)
                                    if !sync.connected {store.status="Убрано локально. Остановка серверных проверок ждёт подключения."}
                                } else {store.status=sync.message}
                            }
                        }
                    }
                } } }
                Button("Показать папку сохранений") {NSWorkspace.shared.open(store.folder)}.font(.caption)
            }.padding(18).frame(minWidth:210,idealWidth:235,maxWidth:280)
            VStack(alignment:.leading,spacing:12) {
                HStack { TextField("Ссылка на оффер LZT Market",text:$store.address).textFieldStyle(.roundedBorder).onSubmit{openOffer()}; Button("Открыть",action:openOffer).buttonStyle(.borderedProminent); Button("Сохранить",action:store.save).keyboardShortcut("s") }
                Text(store.status).font(.caption).foregroundStyle(.secondary).textSelection(.enabled).lineLimit(3)
                Picker("Раздел",selection:$tab) {Text("1. Страница LZT").tag(0);Text("2. Данные аккаунта").tag(1);Text("3. Готовые тексты").tag(2);Text("4. Сервер").tag(3)}.pickerStyle(.segmented)
                if tab == 0 {
                    HStack {Button("Назад"){store.web.goBack()};Button("Восстановить загрузку"){store.reloadPage()};Spacer();Button(store.busy ? "Чтение…" : "Считать страницу") {store.read()}.disabled(store.busy).buttonStyle(.borderedProminent)}
                    Text("Войдите на сайт при необходимости. Раскройте полные списки скинов и чемпионов перед считыванием. Данные читаются только по нажатию кнопки.").font(.caption).foregroundStyle(.secondary)
                    Browser(web:store.web).clipShape(RoundedRectangle(cornerRadius:10))
                } else if tab == 1 {
                    ScrollView {
                        VStack(alignment:.leading,spacing:18) {
                            Text("Добавление предложения").font(.largeTitle.bold())
                            HStack {field("Сервер",\.server); VStack(alignment:.leading) {Text("Тип предложения").font(.caption).foregroundStyle(.secondary); Text("Продажа").frame(maxWidth:.infinity,alignment:.leading).padding(6)} }
                            HStack {field("Уровень",\.level);field("Закупочная цена",\.price)}
                            HStack {field("Чемпионы — количество",\.championCount);field("Скины — количество",\.skinCount);field("Синяя эссенция",\.essence)}
                            HStack {field("Ранг в этом сезоне",\.rank);field("Ранг в предыдущем сезоне",\.previousRank)}
                            Picker("Почта",selection:$store.current.mail) {ForEach(["Не определено","Почта не подтверждена","Доступ к почте передаётся","Авторег, почта подтверждена","Другое — стандартный шаблон"],id:\.self){Text($0)}}
                            Text("Если передаётся доступ к почте, в описании указываются данные почты и гарантия 30 дней. Для неподтверждённой почты сохраняется исходный шаблон.").font(.caption).foregroundStyle(.secondary)
                            field("Скины для названия (можно указать русские названия)",\.featured)
                            field("Ссылка OP.GG",\.opgg)
                            editor("Все чемпионы — через запятую или с новой строки",\.champions)
                            editor("Все скины — через запятую или с новой строки",\.skins,height:160)
                            Button("Выбрать первые 3 скина для названия") {store.current.featured=store.current.names.prefix(3).joined(separator:", ")}
                            DisclosureGroup("Исходный текст / вставка из обычного браузера") {
                                editor("Вставьте текст страницы сюда",\.raw,height:230)
                                Button("Распознать вставленный текст") {store.current=Parser.parse(store.current.raw,into:store.current);store.save()}
                            }
                            if !store.current.missing.isEmpty {Text("Требуют проверки: " + store.current.missing.joined(separator:", ")).font(.caption).foregroundStyle(.orange)}
                        }.padding(8)
                    }
                } else if tab == 3 {
                    ServerPanel(sync:sync,offer:store.current,allOffers:store.offers)
                } else {
                    ScrollView {VStack(alignment:.leading,spacing:18) {
                        Text("Тексты предложения").font(.largeTitle.bold())
                        Text("Шаблон сохраняет ваши постоянные фразы. Перед публикацией проверьте, что они соответствуют аккаунту. Ручные правки ниже сохраняются отдельно от данных.").font(.caption).foregroundStyle(.secondary)
                        Text("Название").font(.headline)
                        TextEditor(text:Binding(get:{store.current.titleOverride.isEmpty ? store.current.title : store.current.titleOverride},set:{store.current.titleOverride=$0})).frame(height:90)
                        Button("Скопировать название"){copyChecked(store.current.titleOverride.isEmpty ? store.current.title : store.current.titleOverride)}
                        Text("Описание").font(.headline)
                        TextEditor(text:Binding(get:{store.current.descriptionOverride.isEmpty ? store.current.description : store.current.descriptionOverride},set:{store.current.descriptionOverride=$0})).frame(height:470)
                        Button("Скопировать описание"){copyChecked(store.current.descriptionOverride.isEmpty ? store.current.description : store.current.descriptionOverride)}
                        Button("Пересобрать тексты из данных"){store.current.titleOverride="";store.current.descriptionOverride=""}
                    }.padding(8)}
                }
            }.padding(20).frame(minWidth:700)
        }.frame(minWidth:1050,minHeight:760).preferredColorScheme(.dark)
        .alert("Есть незаполненные поля",isPresented:$copyWarning) {Button("Скопировать как есть"){store.copy(pendingCopy)};Button("Вернуться",role:.cancel){}} message:{Text(store.current.missing.joined(separator:", "))}
        .onChange(of:store.current) { value in
            var old = store.offers.first(where:{$0.id == value.id})
            old?.updated = value.updated
            if old != value {store.scheduleSave()}
        }
        .task {
            await sync.connect()
            await sync.registerLocal(store.offers)
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds:15_000_000_000)
                if Task.isCancelled {break}
                if sync.connected {await sync.registerLocal(store.offers)} else {await sync.connect()}
            }
        }
        .onReceive(NotificationCenter.default.publisher(for:NSApplication.willTerminateNotification)){_ in store.saveIfNeeded();sync.stop()}
    }
}
extension Store {func tabReset(){status="Введите ссылку и откройте оффер"}}

if CommandLine.arguments.contains("--self-test") {
    let sample = "Сервер: EUNE\nУровень: 150\nЧемпионы: 173\nСкины: 192\nСиняя эссенция: 157071\nЦена: 1500 ₽\nРанг в этом сезоне: Нет\nРанг в предыдущем сезоне: Gold IV\nНе подтверждена почта. Вы можете привязать свою почту\n"
    let opgg = "https://www.op.gg/summoners/EUNE/test-EUNE"
    precondition(Parser.opggLink([opgg]) == opgg)
    precondition(Parser.opggLink(["https://lzt.market/redirect?url=https%3A%2F%2Fwww.op.gg%2Fsummoners%2FEUNE%2Ftest-EUNE"]) == opgg)
    precondition(Parser.opggLink(["window.open('" + opgg + "', '_blank')"]) == opgg)
    precondition(Parser.opggLink([Data(opgg.utf8).base64EncodedString()]) == opgg)
    precondition(Parser.opggLink(["https://fakeop.gg/summoners/test"]) == nil)
    let o=Parser.parse(sample,into:Offer(),links:["https://www.op.gg/summoners/EUNE/test-EUNE"])
    precondition(o.server == "EUNE" && o.level == "150" && o.championCount == "173" && o.skinCount == "192" && o.essence == "157071")
    precondition(o.price == "1500 ₽" && o.rank == "Нет" && o.previousRank == "Unranked" && o.mail == "Почта не подтверждена")
    precondition(o.title.contains("157К СЭ"))
    var a=o;a.mail="Авторег, почта подтверждена"
    precondition(a.description.contains("30 дней") && !a.description.contains("Пожизненная"))
    precondition(o.description.contains("Пожизненная"))
    let transferred=Parser.parse("Вы получите\nдоступ к почте от аккаунта\nГарантия на момент покупки",into:Offer())
    precondition(transferred.mail == "Доступ к почте передаётся")
    precondition(transferred.description.contains("🔒Вы получаете данные в формате почта / пароль от аккаунта / пароль от почты, почту можно сменить"))
    precondition(transferred.description.contains("✅ гарантия на аккаунт 30 дней"))
    precondition(!transferred.description.contains("Пожизненная"))
    let unconfirmed=Parser.parse("Не подтверждена почта. Вы можете привязать свою почту",into:Offer())
    precondition(unconfirmed.description.contains("✅ Пожизненная гарантия на аккаунт"))
    precondition(Parser.match("(?:Цена|Price) ([0-9][0-9 \u{00a0}.,]*[₽$€])", "Аккаунт - Цена 480\u{00a0}₽") == "480\u{00a0}₽")
    let data=try! JSONEncoder().encode(a);precondition(try! JSONDecoder().decode(Offer.self,from:data)==a)
    if let live=try? String(contentsOf: URL(fileURLWithPath:#filePath).deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("Tests/live-page.txt")) {
        let result=Parser.parse(live,into:Offer())
        precondition(result.level == "51" && result.essence == "4142" && result.skinCount == "8" && result.championCount == "37")
        precondition(result.names.count == 8 && result.rank == "Unranked" && result.server == "Европа (Северная и Восточная)")
        precondition(result.price.isEmpty, "Never extract a price from seller reviews")
    }
    print("PASS: parsing, title, email rules, template, persistence")
} else {
    OfferStudioApp.main()
}
struct OfferStudioApp: App {
    var body: some Scene {WindowGroup {ContentView()}.commands {CommandGroup(replacing:.newItem){}}}
}
