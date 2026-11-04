import SwiftUI
import AppKit

struct RemoteOffer: Codable, Identifiable, Equatable {
    var id:String
    var url:String
    var funpay_id:Int?
    var funpay_url:String
    var buy_price:String
    var sell_price:String
    var currency:String
    var monitoring:Int
    var auto_disable:Int
    var verified:Int
    var status:String
    var source_state:String
    var reason:String
    var checked_at:Int?
    var funpay_active:Int?
    var statusText:String {
        if status == "available", let checked=checked_at, Date().timeIntervalSince1970-Double(checked)>600 {return "🟠 Проверяется · данные устарели"}
        switch status {case "available":return "🟢 В наличии";case "removed":return "⚫ Снято с продажи";default:return "🟠 Проверяется"}
    }
}
struct ServerEvent: Codable, Identifiable, Equatable {
    var id:Int
    var ts:Int
    var offer_id:String?
    var level:String
    var message:String
}
struct CreationJob: Codable, Identifiable {
    var source_id:String;var state:String;var detail:String;var updated_at:Int
    var id:String {source_id}
}
struct ServiceHealth: Codable, Identifiable {
    var name:String; var label:String; var state:String; var success_at:Int; var checked_at:Int; var detail:String
    var id:String {name}
}
struct FinanceGroup: Codable {
    var count:Int;var revenue:String;var cost:String;var profit:String;var unknown:Int
}
struct FinanceSummary: Codable {var groups:[String:FinanceGroup]}
struct ServerSnapshot: Codable {
    var finance:FinanceSummary?
    var services:[ServiceHealth]?
    var creation_jobs:[CreationJob]?
    var offers:[RemoteOffer]
    var events:[ServerEvent]
    var paused:Bool
    var worker_online:Bool
    var browser_status:String
    var archived_urls:[String]?
    var interval_seconds:Int
    var server_time:Int
}
struct ServerConnection: Codable {
    var host:String
    var user:String
    var keyPath:String
    var knownHostsPath:String
    var tokenPath:String
}

@MainActor final class ServerSync: ObservableObject {
    @Published var finance:FinanceSummary?
    @Published var jobs:[CreationJob]=[]
    @Published var services:[ServiceHealth]=[]
    @Published var items:[RemoteOffer]=[]
    @Published var events:[ServerEvent]=[]
    @Published var archivedURLs:[String]=[]
    private var pendingArchives:Set<String>=[]
    @Published var message="Сервер не подключён"
    @Published var connected=false
    @Published var paused=true
    @Published var workerOnline=false
    @Published var browserStatus=""
    @Published var syncing=false
    @Published var lastRefresh:Date?
    private var process:Process?
    private var connection:ServerConnection?
    private let folder=FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("OfferStudio/Server")
    private var token:String {guard let c=connection else{return ""};return (try? String(contentsOfFile:c.tokenPath).trimmingCharacters(in:.whitespacesAndNewlines)) ?? ""}
    init() {
        pendingArchives=Set((try? JSONDecoder().decode([String].self,from:Data(contentsOf:folder.appendingPathComponent("pending_archives.json")))) ?? [])
        connection=(try? Data(contentsOf:folder.appendingPathComponent("connection.json"))).flatMap {try? JSONDecoder().decode(ServerConnection.self,from:$0)}
        if let data=try? Data(contentsOf:folder.appendingPathComponent("cache.json")),let cached=try? JSONDecoder().decode(ServerSnapshot.self,from:data) {
            items=cached.offers;events=cached.events;paused=cached.paused;archivedURLs=cached.archived_urls ?? []
            message="Сохранённые статусы; связь с сервером ещё не проверена"
        }
    }
    func item(_ url:String) -> RemoteOffer? {items.first{$0.url.trimmingCharacters(in:CharacterSet(charactersIn:"/")) == url.trimmingCharacters(in:CharacterSet(charactersIn:"/"))}}
    private func request(_ path:String, body:[String:Any]?=nil, timeout:TimeInterval?=nil) async throws -> Data {
        guard connection != nil, !token.isEmpty else {throw NSError(domain:"OfferStudio",code:1,userInfo:[NSLocalizedDescriptionKey:"Нет настройки подключения к серверу"])}
        var req=URLRequest(url:URL(string:"http://localhost:18765"+path)!);req.timeoutInterval=timeout ?? (body == nil ? 8 : 90)
        req.setValue("Bearer "+token,forHTTPHeaderField:"Authorization")
        if let body=body {req.httpMethod="POST";req.httpBody=try JSONSerialization.data(withJSONObject:body);req.setValue("application/json",forHTTPHeaderField:"Content-Type")}
        let (data,response)=try await URLSession.shared.data(for:req)
        guard let http=response as? HTTPURLResponse,(200..<300).contains(http.statusCode) else {
            let error=(try? JSONSerialization.jsonObject(with:data) as? [String:Any])?["error"] as? String
            throw NSError(domain:"OfferStudio",code:2,userInfo:[NSLocalizedDescriptionKey:error ?? "Сервер не подтвердил запрос"])
        }
        return data
    }
    private var connecting=false
    func connect() async {
        guard !connecting else{return};connecting=true;defer{connecting=false}
        guard let c=connection else {message="Настройка сервера не найдена. Локальные функции работают без сервера.";return}
        // Reuse a tunnel left by another window of this application.
        if (try? await request("/v1/status",timeout:2)) != nil {await refresh();return}
        if process?.isRunning != true {
            let p=Process();p.executableURL=URL(fileURLWithPath:"/usr/bin/ssh")
            p.arguments=["-N","-T","-i",c.keyPath,"-o","IdentitiesOnly=yes","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes","-o","UserKnownHostsFile=\""+c.knownHostsPath+"\"","-o","ExitOnForwardFailure=yes","-o","ConnectTimeout=15","-o","ServerAliveInterval=20","-o","ServerAliveCountMax=3","-L","127.0.0.1:18765:127.0.0.1:8765","-L","127.0.0.1:16080:127.0.0.1:6080",c.user+"@"+c.host]
            p.standardOutput=FileHandle.nullDevice;p.standardError=FileHandle.nullDevice
            do {try p.run();process=p;message="Подключаю SSH-туннель…"} catch {message=error.localizedDescription;return}
            for _ in 0..<15 {
                try? await Task.sleep(nanoseconds:1_000_000_000)
                if (try? await request("/v1/status",timeout:2)) != nil {await refresh();return}
                if !p.isRunning {break}
            }
        }
        await refresh()
    }
    func refresh() async {
        guard !syncing else{return};syncing=true;defer{syncing=false}
        do {
            let data=try await request("/v1/status")
            let snapshot=try JSONDecoder().decode(ServerSnapshot.self,from:data)
            finance=snapshot.finance;jobs=snapshot.creation_jobs ?? [];services=snapshot.services ?? [];items=snapshot.offers;events=snapshot.events;archivedURLs=snapshot.archived_urls ?? [];paused=snapshot.paused;workerOnline=snapshot.worker_online;browserStatus=snapshot.browser_status
            connected=true;lastRefresh=Date();message="Синхронизировано · "+Date().formatted(date:.omitted,time:.shortened)
            try? data.write(to:folder.appendingPathComponent("cache.json"),options:.atomic)
        } catch {connected=false;workerOnline=false;message="Сервер недоступен: "+error.localizedDescription}
    }
    private func persistArchives() throws {
        try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
        try JSONEncoder().encode(Array(pendingArchives)).write(to:folder.appendingPathComponent("pending_archives.json"),options:.atomic)
    }
    func queueArchive(_ url:String) async -> Bool {
        guard !url.isEmpty else{return true}
        pendingArchives.insert(url)
        do {try persistArchives()} catch {message="Не удалось сохранить удаление: "+error.localizedDescription;return false}
        if connected {await flushArchives();await refresh()}
        return true
    }
    private func flushArchives() async {
        for url in Array(pendingArchives) {
            do {
                _=try await request("/v1/archive",body:["url":url])
                pendingArchives.remove(url);try persistArchives()
            } catch {message="Остановка мониторинга ждёт связи с сервером: "+error.localizedDescription;return}
        }
    }
    func restoreForAdding(_ url:String) async {
        guard !url.isEmpty else{return}
        if pendingArchives.contains(url) {await flushArchives()}
        if archivedURLs.contains(url) {
            do {_=try await request("/v1/restore",body:["url":url]);await refresh()} catch {message="Не удалось восстановить мониторинг: "+error.localizedDescription}
        }
    }
    func registerLocal(_ offers:[Offer]) async {
        guard connected else{return}
        await flushArchives()
        for offer in offers where !offer.url.isEmpty && item(offer.url) == nil && !pendingArchives.contains(offer.url) && !archivedURLs.contains(offer.url) {
            do {
                _=try await request("/v1/offers",body:["url":offer.url,"local_id":offer.id,"title":offer.title,"buy_price":offer.price,"funpay_url":"","monitoring":true,"auto_disable":false])
            } catch {message="Не удалось добавить аккаунт в мониторинг: "+error.localizedDescription;return}
        }
        await refresh()
    }
    @Published var creating=false
    @Published var creationResult=""
    func creationPayload(_ offer:Offer) -> [String:Any] {
        ["url":offer.url,"title":offer.titleOverride.isEmpty ? offer.title : offer.titleOverride,"description":offer.descriptionOverride.isEmpty ? offer.description : offer.descriptionOverride,"buy_price":offer.price,"server":offer.server,"level":offer.level,"championCount":offer.championCount,"skinCount":offer.skinCount,"rank":offer.rank,"local_id":offer.id]
    }
    func enqueueBatch(_ offers:[Offer]) async {
        do {
            _=try await request("/v1/batch",body:["items":offers.map{creationPayload($0)}]);await refresh();creationResult="Лоты добавлены в серверную очередь. Она работает после закрытия приложения."
        } catch {creationResult="Очередь не сохранена: "+error.localizedDescription}
    }
    func retryBatch(_ id:String) async {
        do {_=try await request("/v1/batch/retry",body:["source_id":id]);await refresh()} catch {creationResult=error.localizedDescription}
    }
    func cancelBatch() async {
        do {_=try await request("/v1/batch/cancel",body:[:]);await refresh()} catch {creationResult=error.localizedDescription}
    }
    func createOffer(_ offer:Offer) async {
        guard !creating else{return};creationResult="";creating=true;defer{creating=false}
        do {
            _=try await request("/v1/create",body:["url":offer.url,"title":offer.titleOverride.isEmpty ? offer.title : offer.titleOverride,"description":offer.descriptionOverride.isEmpty ? offer.description : offer.descriptionOverride,"buy_price":offer.price,"server":offer.server,"level":offer.level,"championCount":offer.championCount,"skinCount":offer.skinCount,"rank":offer.rank,"local_id":offer.id])
            await refresh();creationResult="Лот создан на FunPay и привязан. Цена ×2,5, автовыдача выключена."
        } catch {creationResult="Создание не подтверждено: "+error.localizedDescription}
    }
    func link(_ offer:Offer, funpayURL:String, sellPrice:String, monitoring:Bool, autoDisable:Bool) async {
        do {
            _=try await request("/v1/offers",body:["url":offer.url,"local_id":offer.id,"funpay_url":funpayURL,"title":offer.title,"buy_price":offer.price,"sell_price":sellPrice,"monitoring":monitoring,"auto_disable":autoDisable])
            await refresh();message="Связь сохранена на сервере. Владение FunPay-лотом проверено."
        } catch {message="Не удалось сохранить связь: "+error.localizedDescription}
    }
    func control(_ action:String) async {
        do {_=try await request("/v1/control",body:["action":action]);await refresh()} catch {message=error.localizedDescription}
    }
    func openBrowser() {
        guard connected else {message="Сначала подключитесь к серверу";return}
        Task {await control("auth");NSWorkspace.shared.open(URL(string:"http://localhost:16080/vnc.html?autoconnect=1&resize=scale")!)}
    }
    func stop(){process?.terminate();process=nil}
}

struct FinanceRow:View {
    var kind:String
    var group:FinanceGroup
    var title:String {switch kind {case "CLOSED":return "Завершённые";case "PAID":return "Ожидаемые";case "REFUNDED":return "Полные возвраты";default:return "Частичные возвраты"}}
    var body:some View {
        VStack(alignment:.leading) {
            Text("\(title): \(group.count)").bold()
            Text("Выручка: \(group.revenue) ₽ · Закупка: \(group.cost) ₽ · Прибыль: \(group.profit) ₽").font(.caption)
            if group.unknown>0 {Text("Нужны данные для расчёта: \(group.unknown)").font(.caption).foregroundStyle(.orange)}
        }
    }
}

struct ServerPanel:View {
    @ObservedObject var sync:ServerSync
    var offer:Offer
    var allOffers:[Offer]
    @State private var selectedBatch:Set<String>=[]
    @State private var confirmBatch=false
    @State private var funpayURL=""
    @State private var sellPrice=""
    @State private var monitoring=true
    @State private var autoDisable=true
    @State private var editing=false
    func fill() {
        let row=sync.item(offer.url)
        funpayURL=row?.funpay_url ?? "";sellPrice=row?.sell_price ?? ""
        monitoring=row.map{$0.monitoring==1} ?? true;autoDisable=row.map{$0.auto_disable==1} ?? true
    }
    @ViewBuilder var financeSection:some View {
        if let finance=sync.finance {
            Text("Продажи и возвраты").font(.title2.bold())
            ForEach(["CLOSED","PAID","REFUNDED","PARTIALLY_REFUNDED"],id:\.self) {key in
                if let group=finance.groups[key] {FinanceRow(kind:key,group:group)}
            }
            Text("Без вычета комиссии. Ожидаемая прибыль учитывается отдельно. Возврат включает потерю закупки.").font(.caption)
            Divider()
        }
    }
    @ViewBuilder var batchSection:some View {
            Text("Массовое создание").font(.title2.bold())
            Text("Отметьте готовые аккаунты. Цена закупки ×2,5; создание по одному на сервере. Неоднозначные результаты требуют проверки и не повторяются автоматически.").font(.caption)
            ForEach(allOffers.filter{!$0.url.isEmpty && sync.item($0.url)?.funpay_id == nil}) {candidate in
                Toggle(isOn:Binding(get:{selectedBatch.contains(candidate.id)},set:{if $0 {selectedBatch.insert(candidate.id)} else {selectedBatch.remove(candidate.id)}})) {
                    Text(candidate.url+" · закупка "+candidate.price).font(.caption)
                }
            }
            HStack {
                Button("Создать выбранные: \(selectedBatch.count)"){confirmBatch=true}.disabled(selectedBatch.isEmpty || !sync.connected)
                Button("Отменить ожидающие"){Task{await sync.cancelBatch()}}.disabled(!sync.connected)
            }
            ForEach(sync.jobs) {job in
                HStack {
                    Text("#"+job.source_id+" · "+job.state+" · "+job.detail).font(.caption).textSelection(.enabled)
                    if job.state == "review" || job.state == "cancelled" {Button("Повторить"){Task{await sync.retryBatch(job.source_id)}}}
                }
            }
            Divider()
    }
    var body:some View {
        ScrollView {VStack(alignment:.leading,spacing:18) {
            Text("Сервер и мониторинг").font(.largeTitle.bold())
            HStack {
                Label(sync.connected ? "Сервер подключён" : "Нет связи",systemImage:sync.connected ? "network":"network.slash")
                Text(sync.paused ? "⏸ Общая пауза":"Проверка раз в 5 минут")
                Spacer()
                Button("Подключить / обновить"){Task{await sync.connect()}}
            }
            Text(sync.message).font(.caption).textSelection(.enabled)
            Text(sync.workerOnline ? sync.browserStatus : "Браузерный процесс сервера не подтверждён").font(.caption).foregroundStyle(.secondary)
            ForEach(sync.services) {service in
                VStack(alignment:.leading,spacing:3) {
                    HStack {Text(service.label).bold();Spacer();Text(sync.connected ? (["ok":"Работает","error":"Ошибка","stale":"Данные устарели","unknown":"Не проверено","paused":"Пауза"][service.state] ?? service.state) : "нет связи")}
                    Text(service.detail).font(.caption).foregroundStyle(.secondary)
                    Text(service.success_at > 0 ? "Успешная операция: "+Date(timeIntervalSince1970:Double(service.success_at)).formatted() : "Успешных операций пока нет").font(.caption)
                }.padding(8).background(Color.white.opacity(0.04)).clipShape(RoundedRectangle(cornerRadius:8))
            }
            HStack {
                Button("Проверить сейчас"){Task{await sync.control("check")}}
                Button(sync.paused ? "Запустить мониторинг":"Приостановить"){Task{await sync.control(sync.paused ? "resume":"pause")}}
                Button("Авторизация LZT на сервере",action:sync.openBrowser)
            }.disabled(!sync.connected)
            Divider()
            financeSection
            Text("Связь выбранного аккаунта").font(.title2.bold())
            Text(offer.url.isEmpty ? "Сначала добавьте аккаунт во вкладке страницы LZT" : offer.url).textSelection(.enabled)
            if let row=sync.item(offer.url) {
                Text(row.statusText).font(.headline)
                Text(row.reason).font(.caption)
                if let ts=row.checked_at {Text("Последняя проверка: "+Date(timeIntervalSince1970:Double(ts)).formatted()).font(.caption)}
            }
            VStack(alignment:.leading) {Text("Ваш оффер FunPay").font(.caption);TextField("https://funpay.com/lots/offer?id=12345678",text:Binding(get:{funpayURL},set:{funpayURL=$0;editing=true})).textFieldStyle(.roundedBorder)}
            HStack {Text("Закупочная цена: "+(offer.price.isEmpty ? "не указана":offer.price));Spacer();TextField("Цена продажи",text:Binding(get:{sellPrice},set:{sellPrice=$0;editing=true})).frame(width:180).textFieldStyle(.roundedBorder)}
            Toggle("Проверять этот аккаунт на сервере",isOn:Binding(get:{monitoring},set:{monitoring=$0;editing=true}))
            Toggle("При подтверждённой продаже на LZT снимать связанный оффер FunPay",isOn:Binding(get:{autoDisable},set:{autoDisable=$0;editing=true}))
            Text("При сохранении сервер проверит, что FunPay-лот принадлежит вам и находится в категории аккаунтов LoL. Цена продажи считывается из FunPay. Снятие меняет только «Активное»; обратное включение автоматически не выполняется.").font(.caption).foregroundStyle(.secondary)
            Button("Сохранить связь на сервере") {Task{await sync.link(offer,funpayURL:funpayURL,sellPrice:sellPrice,monitoring:monitoring,autoDisable:autoDisable);editing=false;fill()}}.buttonStyle(.borderedProminent).disabled(!sync.connected || offer.url.isEmpty)
            Button(sync.creating ? "Создание…" : "Создать лот FunPay · закупка ×2,5") {Task{await sync.createOffer(offer);editing=false;fill()}}
                .disabled(!sync.connected || sync.creating || offer.url.isEmpty || sync.item(offer.url)?.funpay_id != nil)
            if !sync.creationResult.isEmpty {Text(sync.creationResult).font(.callout).textSelection(.enabled)}
            Text("Название и описание берутся из готовых текстов. Автовыдача выключена; лот создаётся активным.").font(.caption).foregroundStyle(.secondary)
            Divider()
            batchSection
            Text("Все серверные лоты").font(.title2.bold())
            ForEach(sync.items) {row in
                VStack(alignment:.leading,spacing:4) {
                    HStack {Text("LZT #"+row.id).bold();Text("→ FunPay #"+(row.funpay_id.map(String.init) ?? "—"));Spacer();Text(row.statusText)}
                    Text("Закупка: \(row.buy_price) · Продажа: \(row.sell_price) \(row.currency)").font(.caption)
                    Text(row.reason).font(.caption).foregroundStyle(.secondary)
                }.padding(10).background(Color.white.opacity(0.04)).clipShape(RoundedRectangle(cornerRadius:8))
            }
            Text("Журнал сервера").font(.title2.bold())
            ForEach(sync.events.prefix(60)) {event in
                HStack(alignment:.top) {
                    Text(Date(timeIntervalSince1970:Double(event.ts)).formatted(date:.numeric,time:.standard)).font(.caption).frame(width:145,alignment:.leading)
                    Text("[\(event.level)] #\(event.offer_id ?? "—") · \(event.message)").font(.caption).textSelection(.enabled)
                }
            }
        }.padding(8)}
        .alert("Создать выбранные лоты FunPay?",isPresented:$confirmBatch) {
            Button("Создать",role:.destructive){let chosen=allOffers.filter{selectedBatch.contains($0.id)};Task{await sync.enqueueBatch(chosen);selectedBatch.removeAll()}}
            Button("Отмена",role:.cancel){}
        } message: {Text("Будут созданы активные лоты, автовыдача выключена. Проверьте названия, описания и закупочные цены выбранных аккаунтов.")}
        .onAppear{fill()}
        .onChange(of:offer.url){_ in editing=false;sync.creationResult="";fill()}
        .onChange(of:sync.items){_ in if !editing {fill()}}
    }
}
