# sync_ssd

Backup de mão única da pasta local do **Google Drive para Desktop** (ou de qualquer pasta do Windows) para **um SSD externo específico**. Python puro, sem dependências.

A ideia é simples: você pluga o SSD, o script reconhece que é *aquele* SSD, copia o que mudou e guarda a versão anterior de cada arquivo sobrescrito. O que você apagou na origem sai do espelho para uma quarentena, de onde ainda dá para recuperar. Ele nunca apaga nada de imediato e se recusa a rodar quando algo parece errado.

Um segundo script, independente, **confere o resultado**: compara todos os arquivos do Drive com o SSD e diz se estão idênticos. Os dois mostram um resumo em português claro, avisam pelo Windows quando algo dá errado no backup automático e mantêm um histórico que abre no navegador.

> **English summary.** One-way backup from a local Google Drive folder to one specific external SSD, identified by volume serial number plus an identity file. Never deletes outright: overwritten files go to `versoes-antigas/`, files removed at the source go to `quarentena/` (with `--quarentena`), both kept for a configurable period. Detects moved/renamed files, refuses to run if too many files change at once (ransomware guard), checks free space, and skips files Google Drive has not downloaded yet. A separate, read-only checker (`conferir_ssd.py`, independent comparison code) compares every file by existence, size and modification time. Output modes: a friendly summary screen (default), `--detalhado` (technical log), `--silencioso` (no output, Windows toast on problems — for scheduled runs) and `--json`. Every real run is recorded in `historico.json` and rendered to `historico.html`. Double-click `.bat` launchers included. Windows only, Python 3.8+, no third-party packages. The rest of this README is in Portuguese.

## Arquivos

Na pasta principal ficam só os atalhos que você clica. O programa fica na pasta `programa`, que você não precisa abrir.

```
SyncSSD\
├── Fazer backup.bat          backup (com quarentena) seguido de conferência
├── Simular backup.bat        mostra o que o backup faria, sem copiar nada
├── Conferir SSD.bat          só a conferência
├── Limpar guardados.bat      o que passou do prazo nas pastas de guarda, com confirmação
├── Configurar limpeza.bat    apagar o que vence: sozinho, com autorização ou nunca
├── Ver histórico.bat         abre o histórico no navegador
├── README.md
└── programa\
    ├── sync_ssd.py                    o backup
    ├── conferir_ssd.py                a conferência (só leitura, código de comparação próprio)
    ├── visual_ssd.py                  apresentação: tela, notificação, histórico
    ├── sync_ssd_config.example.json   modelo de configuração
    ├── sync_ssd_config.json           a sua configuração (criada pelo --registrar; fora do Git)
    └── test_*.py                      testes
```

---

## O que ele garante

| Garantia | Como |
|---|---|
| Só grava no SSD certo | Exige o **número de série do volume** e um **arquivo de identidade** com um ID gerado no registro. Outro disco com a mesma letra ou o mesmo nome é recusado. |
| Nunca apaga de imediato | Arquivos que saíram do Drive ficam no SSD como "órfãos". Com `--quarentena` (o padrão do `Fazer backup.bat`), saem do espelho para `_sync_ssd/quarentena/<data-hora>/` e ficam lá por 90 dias. |
| Guarda a versão anterior | Antes de sobrescrever, move o arquivo antigo para `_sync_ssd/versoes-antigas/<data-hora>/`. Ficam por 90 dias. |
| Nunca deixa arquivo pela metade | Copia para um `.sync_tmp` e só então troca pelo definitivo. |
| Não duplica ao mover pasta | Pasta renomeada ou movida no Drive é movida dentro do SSD, sem recopiar. |
| Trava contra ransomware | Se muitos arquivos mudarem de uma vez, aborta sem copiar nada. |
| Não estoura o disco | Confere o espaço livre antes de começar. |
| Não roda duas vezes ao mesmo tempo | Lock file local. |
| Não força download do Drive | Arquivos que o Drive ainda não baixou (modo *stream*) são pulados com aviso. |
| Resultado conferido | `conferir_ssd.py` compara **todos** os arquivos (existência, tamanho, data) com código próprio, sem reaproveitar a lógica do backup. |
| Você fica sabendo | Resumo claro na tela, notificação do Windows quando o backup automático tem problema, e um histórico de todas as execuções. |

## Requisitos

- Windows 10 ou 11.
- Python 3.8 ou mais novo (a lógica também roda em macOS/Linux, mas o reconhecimento do SSD por número de série usa a API do Windows).
- Nenhum pacote externo.
- Recomendado: SSD formatado em **exFAT** ou **NTFS**. FAT32 não aceita arquivos acima de 4 GB.

## Instalação (para quem fez fork)

1. Clone ou baixe o repositório em uma pasta fixa, por exemplo `C:\Ferramentas\sync_ssd\`, e abra um terminal na pasta `programa` dentro dela. Os comandos deste README são rodados de lá.
2. Copie `sync_ssd_config.example.json` para `sync_ssd_config.json` e ajuste pelo menos `origem`. Ou pule esta etapa e deixe o `--registrar` criar o arquivo com os padrões.
3. Plugue o SSD e registre-o (troque `D:` pela letra dele):

   ```bat
   python sync_ssd.py --registrar D:
   ```

   Isso grava `D:\_sync_ssd\IDENTIDADE_SSD.txt` no SSD e salva serial + id em `sync_ssd_config.json`.

4. Veja o que o primeiro backup faria (ou dê dois cliques em `Simular backup.bat`):

   ```bat
   python sync_ssd.py --simular
   ```

5. Rode o primeiro backup. Como o SSD está vazio, 100% dos arquivos são "novos" e a trava de segurança dispara. Isso é esperado. Passe `--forcar` só desta vez:

   ```bat
   python sync_ssd.py --forcar
   ```

6. Confira o resultado. Deve terminar com `✔ SSD idêntico ao Drive`:

   ```bat
   python conferir_ssd.py
   ```

7. A partir daí, dê dois cliques em `Fazer backup.bat`, ou agende (veja [Agendamento](#agendamento)).

## Sem linha de comando

Os `.bat` da pasta rodam tudo com dois cliques e deixam a janela aberta com o resultado até você apertar uma tecla:

- **`Fazer backup.bat`**: backup com `--quarentena` e, se ele rodou, a conferência. Se a limpeza estiver em "perguntar" e algo tiver vencido, pergunta no fim se pode apagar.
- **`Simular backup.bat`**: o que o backup faria, sem copiar.
- **`Conferir SSD.bat`**: só a conferência.
- **`Limpar guardados.bat`**: mostra o que passou do prazo em versões antigas e quarentena e pergunta se apaga. Funciona com qualquer escolha de limpeza.
- **`Configurar limpeza.bat`**: um menu para escolher o que acontece com o que passa do prazo e mudar os prazos.
- **`Ver histórico.bat`**: abre o histórico no navegador.

Para ter um botão na área de trabalho: botão direito no `.bat` → *Mostrar mais opções* → *Enviar para* → *Área de trabalho (criar atalho)*.

Os `.bat` chamam `python`, que precisa estar no PATH (o instalador do python.org oferece essa opção).

## O que aparece na tela

```
  BACKUP  Meu Drive → SSD EXTERNO (E:)
  30/09/2026, 10:31 · último backup há 18 horas

  ✚ Novos (2)
      Trabalho › Relatórios
        relatorio-setembro.docx                                      48,2 KB
        anotacoes.md                                                  2,8 KB

  ↻ Atualizados (1) · versão anterior guardada por 90 dias
      Trabalho › Planilhas
        orcamento-2026.xlsx                                          31,6 KB

  ⇢ Tirados do espelho (1) · saíram do Drive · ficam na quarentena por 90 dias
      Modelos
        modelo-antigo.dotx

  ──────────────────────────────────────────────────────────
  ✔ Tudo certo · 3 copiados (82,6 KB), 1 para a quarentena em 0,4 s

  Guardados no SSD
    Versões antigas   3 arquivos · 74,0 KB · a mais antiga sai em 27/12
    Quarentena        1 arquivo · 45,1 KB · a mais antiga sai em 29/12
```

- O resultado vem numa linha com ✔ (tudo certo), ⚠ (atenção) ou ✖ (não rodou), em português, sem códigos.
- Cada pasta aparece uma vez, com os arquivos embaixo. Listas longas mostram os 15 maiores e "... e mais N"; a lista completa fica no log.
- Cópias longas (mais de 50 arquivos ou 100 MB) mostram uma barra de progresso com o tempo restante.
- **Guardados no SSD** mostra o que está nas duas pastas de guarda e quando o item mais antigo de cada uma expira.
- Cores e símbolos aparecem no terminal do Windows 10/11. Fora de um terminal, com a variável `NO_COLOR` definida, ou num console que não aceite os símbolos, a saída vira texto simples.

## Uso

```
python sync_ssd.py                       backup incremental
python sync_ssd.py --simular             mostra o que faria, sem tocar em nada
python sync_ssd.py --forcar              ignora a trava de segurança
python sync_ssd.py --orfaos              só lista o que está no SSD e saiu do Drive (não copia nada)
python sync_ssd.py --quarentena          backup + move órfãos para _sync_ssd/quarentena/<data>/
python sync_ssd.py --verificar [N]       backup + confere o SHA-256 de N arquivos (padrão 200; 0 = todos)
python sync_ssd.py --baixar              copia também arquivos que o Drive ainda não baixou
python sync_ssd.py --status              resultado da última execução
python sync_ssd.py --historico           abre o histórico no navegador
python sync_ssd.py --limpar              mostra o que venceu nas pastas de guarda e pergunta se apaga
python sync_ssd.py --configurar-limpeza  menu: apagar o que vence sozinho, com autorização ou nunca
python sync_ssd.py --testar-notificacao  envia uma notificação de teste do Windows
python sync_ssd.py --alertar-se-velho 7  se o SSD não estiver plugado e o último backup tiver
                                         mais de 7 dias, mostra uma notificação (no máximo 1x/dia)
python sync_ssd.py --config ARQUIVO      usa outro sync_ssd_config.json
python sync_ssd.py --registrar D:        registra o SSD (com --forcar para trocar de disco)
```

Os flags combinam: `python sync_ssd.py --quarentena --verificar 500` faz backup, quarentena e verificação.

### Modos de saída

O backup é sempre o mesmo; estes flags mudam **só como o resultado aparece**. Os logs em arquivo são sempre técnicos, em qualquer modo.

| Flag | Para quê | O que aparece |
|---|---|---|
| (nenhum) | uso normal, `.bat` | a tela resumida acima |
| `--detalhado` | investigar um problema | o log técnico, com hora em cada linha e caminhos completos |
| `--silencioso` | agendamento | nada; notificação do Windows se o resultado for problema |
| `--json` | integração com outro programa | um único objeto JSON no stdout |

`--silencioso` notifica nos códigos 1, 3, 4, 5, 6, 8 e 11. Não notifica sucesso (0), SSD desplugado (2: para isso existe o `--alertar-se-velho`) nem execução já em andamento (7). A notificação é um *toast* do Windows; se não for possível, aparece uma caixa de mensagem.

O JSON traz `codigo`, `resultado` (`ok`, `aviso` ou `erro`), `mensagem`, as listas `novos`, `atualizados`, `movidos`, `quarentena`, `orfaos` e `falhas`, a `verificacao` (se pedida) e o `inventario` das pastas de guarda. Sai também nos erros, com `codigo` e `mensagem`.

### Códigos de saída

Úteis para o Agendador de Tarefas ou para um script que chame este.

| Código | Significado |
|---|---|
| 0 | sucesso |
| 1 | backup feito, mas alguns arquivos falharam (veja o log) |
| 2 | SSD de backup não encontrado |
| 3 | origem indisponível (Google Drive fechado?) |
| 4 | origem vazia, abortado por segurança |
| 5 | trava de segurança acionada, nada copiado |
| 6 | espaço insuficiente no SSD, nada copiado |
| 7 | já existe outra execução em andamento |
| 8 | verificação de hash encontrou divergências |
| 9 | alerta: último backup velho demais |
| 10 | configuração não encontrada (rode `--registrar`) |
| 11 | erro inesperado (veja o log) |

## Versões antigas e quarentena

São duas pastas separadas, para que dê para saber só de olhar no Explorer o que é cada coisa:

| Pasta | O que vai para lá | Quando | Prazo |
|---|---|---|---|
| `_sync_ssd\versoes-antigas\<data-hora>\` | a versão anterior de um arquivo que você editou | toda vez que o backup sobrescreve algo | `dias_versoes` (90) |
| `_sync_ssd\quarentena\<data-hora>\` | arquivos que você apagou (ou tirou) da origem | só com `--quarentena` | `dias_quarentena` (90) |

Dentro de cada pasta datada, a estrutura de pastas é a mesma do espelho. **Para recuperar, copie o arquivo de volta** com o Explorer. A data no nome da pasta é *quando o backup guardou*; a data do arquivo lá dentro é *de quando ele é*.

O que acontece quando o prazo vence depende da escolha de limpeza (próxima seção). `max_versoes_gb` limita também o espaço das versões antigas, e segue a mesma escolha.

Versões até a 2.0 guardavam os dois tipos juntos em `_sync_ssd\versoes\`. Na primeira execução real da 2.1, essa pasta é movida automaticamente para `versoes-antigas\` (não há como separar o que era quarentena).

## Limpeza do que está guardado

Você escolhe o que acontece com o que passa do prazo, nas duas pastas de guarda:

| Escolha | Valor em `apagar_vencidos` | O que acontece |
|---|---|---|
| **Apagar sozinho** (padrão) | `automatico` | o backup apaga o que venceu, sem perguntar, inclusive no agendamento |
| **Perguntar antes** | `perguntar` | só apaga com a sua confirmação |
| **Nunca apagar** | `nunca` | não apaga nem pergunta; você limpa quando quiser |

**Para mudar, dê dois cliques em `Configurar limpeza.bat`.** O menu mostra a escolha atual, pede 1, 2 ou 3 e os prazos (Enter mantém o que está). Quem preferir pode editar `apagar_vencidos`, `dias_versoes` e `dias_quarentena` em `programa\sync_ssd_config.json`.

Como cada escolha se comporta:

- **`perguntar`, pelo `Fazer backup.bat`:** no fim do backup aparece o que venceu (quantas pastas, quantos arquivos, tamanho, quando foram guardadas) e a pergunta *Apagar agora? [S/N]*. Com N, nada é apagado, e a pergunta volta no próximo backup.
- **`perguntar`, no agendamento:** como não há janela para perguntar, **nada é apagado**. Você recebe uma notificação dizendo que há itens vencidos, no máximo uma vez por dia, e decide pelo `Fazer backup.bat` ou pelo `Limpar guardados.bat`.
- **`nunca`:** nada é apagado e nada é perguntado. A tela do backup e o histórico avisam quando há pastas vencidas.
- **`Limpar guardados.bat`** (ou `--limpar`) funciona com qualquer escolha: mostra o que venceu e pergunta. Se nada venceu, diz quando vence o próximo.

> **No agendamento, o padrão é apagar sozinho.** Se você quer autorizar cada limpeza, escolha "Perguntar antes" no `Configurar limpeza.bat`: o agendamento passa a só avisar, e quem apaga é você.

Toda limpeza confirmada fica registrada no log e no histórico.

## Histórico

Toda execução real (backup, conferência ou limpeza; simulações não) acrescenta um registro a `_sync_ssd\historico.json` no SSD, que guarda as 200 mais recentes, e regenera `historico.html` ao lado dele. A página mostra o último backup, a última conferência, o que está guardado em versões antigas e em quarentena, e uma tabela com todas as execuções. Uma cópia da página fica em `%LOCALAPPDATA%\SyncSSD\historico.html`, para abrir mesmo com o SSD desplugado.

Para abrir: `Ver histórico.bat` ou `python sync_ssd.py --historico`.

## Conferência (`conferir_ssd.py`)

```
python conferir_ssd.py                 confere e mostra o resultado
python conferir_ssd.py --limite 0      lista todas as divergências (padrão: até 30 por tipo)
python conferir_ssd.py --extras        lista também o que só existe no SSD
python conferir_ssd.py --config ARQ    usa outro sync_ssd_config.json
```

Aceita os mesmos modos de saída do backup: `--detalhado`, `--silencioso` (notifica se houver divergência) e `--json`.

**Só leitura.** Nunca copia, move ou apaga. Grava apenas o próprio log e o registro no histórico.

**O que compara.** Todos os arquivos da origem contra o espelho no SSD, pelos metadados: se o arquivo existe, se o tamanho é igual e se a data de modificação bate (dentro de `tolerancia_seg`). Metadados não fazem o Google Drive baixar nada, então a conferência leva poucos segundos mesmo com o Drive em modo *stream*. Para conferir o **conteúdo** byte a byte, use `sync_ssd.py --verificar` (mais lento; ele lê os arquivos).

**Por que é um script separado.** A conferência reimplementa a varredura e a comparação em vez de importar o `sync_ssd.py`. Se houvesse um erro na lógica do backup, a mesma lógica usada para conferir o confirmaria. Os dois compartilham apenas o `sync_ssd_config.json` (origem, exclusões, tolerância), a identificação do SSD e o `visual_ssd.py`, que só cuida da apresentação. Um dos testes roda os dois em sequência e exige que concordem.

**Quando rodar.** Depois do backup; o `Fazer backup.bat` já faz isso. Antes não é necessário: comparar os dois lados é o primeiro passo do próprio backup, e é o que o `--simular` mostra.

O resultado separa as divergências por tipo:

| Tipo | Significa | Conta como divergência? |
|---|---|---|
| Faltando no SSD | Existe no Drive e não no SSD. Se o arquivo ainda não foi baixado pelo Drive, a conferência avisa. | sim |
| Tamanho diferente | Mesmo caminho, tamanhos diferentes. | sim |
| SSD desatualizado | O Drive tem data mais recente. | sim |
| SSD mais novo que o Drive | O arquivo do SSD foi mexido depois do backup. | sim |
| Só no SSD | Saiu do Drive e ainda não foi para a quarentena. Veja com `--extras`; para tirar do espelho, `sync_ssd.py --quarentena`. | não |

Divergências logo após um backup costumam ser arquivos editados durante a execução: rode o backup de novo. Se persistirem, a lista diz quais são.

| Código | Significado |
|---|---|
| 0 | SSD idêntico ao Drive |
| 1 | há divergências |
| 2 | SSD de backup não encontrado |
| 3 | origem indisponível (Google Drive fechado?) |
| 4 | origem vazia |
| 10 | configuração não encontrada (rode `sync_ssd.py --registrar`) |

## Configuração

O arquivo `sync_ssd_config.json` fica ao lado do script, na pasta `programa`. Se não existir ali, o script procura em `%LOCALAPPDATA%\SyncSSD\`. Qualquer chave omitida usa o valor padrão.

| Chave | Padrão | Descrição |
|---|---|---|
| `serial` | — | Número de série do volume do SSD. Preenchido pelo `--registrar`. |
| `id` | — | UUID gerado no registro; também está no `IDENTIDADE_SSD.txt` do SSD. |
| `origem` | `G:\Meu Drive` | Pasta a copiar. Pode ser qualquer pasta, não só o Drive. |
| `pasta_destino` | `""` | Subpasta no SSD onde o espelho fica. Vazio = raiz do SSD. |
| `excluir` | atalhos `.gdoc` etc., `desktop.ini`, `thumbs.db`, `~$*`, `.~lock*` | Padrões [fnmatch](https://docs.python.org/3/library/fnmatch.html), testados no nome e no caminho relativo (com `/`). Ex.: `"node_modules"`, `"*.iso"`, `"Fotos/RAW/*"`. |
| `tolerancia_seg` | `2` | Diferença de data de modificação ignorada. exFAT guarda datas com precisão de 2 s. |
| `limite_abs` | `300` | Trava: dispara se **mais que isso** de arquivos mudarem... |
| `limite_pct` | `0.25` | ...**e** isso for mais que 25% do total. As duas condições precisam valer. |
| `dias_versoes` | `90` | Por quantos dias as versões antigas ficam guardadas. |
| `dias_quarentena` | `90` | Por quantos dias os arquivos em quarentena ficam guardados. |
| `apagar_vencidos` | `automatico` | O que fazer com o que passou do prazo: `automatico`, `perguntar` ou `nunca`. Valor desconhecido vale como `perguntar`. Mais fácil de mudar pelo `Configurar limpeza.bat`. |
| `max_versoes_gb` | `0` | Teto de espaço para as versões antigas. Ao passar, as mais antigas saem primeiro. `0` = sem teto. |
| `margem_espaco_gb` | `2` | Espaço que deve sobrar livre no SSD depois do backup. |
| `caminhos_longos` | `true` | Usa o prefixo `\\?\` para aceitar caminhos com mais de 260 caracteres. Desligue se algum caminho de rede se comportar mal. |
| `horas_lock_velho` | `12` | Um lock mais velho que isso é considerado abandonado (queda de energia, por exemplo). |

### O que é público e o que não é

`serial` e `id` não são senhas, mas juntos são exatamente o que identifica o seu SSD para o script. Se o repositório for público, prefira não versionar o `sync_ssd_config.json` real. O `.gitignore` deste repositório já o ignora; versione o `sync_ssd_config.example.json` no lugar. Os logs, o histórico e o `ultimo_resultado.json` contêm nomes de arquivos do seu Drive e ficam fora da pasta do repositório (no SSD e em `%LOCALAPPDATA%`).

## O que fica no SSD

```
D:\
├── (espelho do Drive, ou dentro de pasta_destino)
└── _sync_ssd\
    ├── IDENTIDADE_SSD.txt              id + serial + data do registro. Não apague.
    ├── ultimo_resultado.json           resumo da última execução
    ├── historico.json                  últimas 200 execuções
    ├── historico.html                  o histórico, para abrir no navegador
    ├── logs\2026-09.log                log técnico do backup, um arquivo por mês
    ├── logs\conferencia-2026-09.log    log técnico da conferência
    ├── versoes-antigas\
    │   └── 2026-09-29_163650\          uma pasta por execução que sobrescreveu algo,
    │       └── Trabalho\...            com a mesma estrutura de pastas do espelho
    └── quarentena\
        └── 2026-09-30_180000\          uma pasta por execução com --quarentena que tirou algo
            └── Modelos\...
```

O espelho são arquivos comuns. **Para restaurar, basta copiar de volta** com o Explorer. Não há formato proprietário nem banco de dados.

No computador, em `%LOCALAPPDATA%\SyncSSD\`, ficam o log local, o `ultimo_resultado.json` (para `--status` e `--alertar-se-velho` funcionarem sem o SSD plugado), o lock, a cópia do `historico.html`, e o `conferencia.log` e o `ultima_conferencia.json` da conferência.

## Agendamento

A forma mais simples é rodar a cada 30 minutos. Quando o SSD não está plugado o script sai em menos de um segundo com código 2, então o custo é zero. Use `pythonw.exe` (não abre janela) e `--silencioso` (você só é avisado quando há problema). A conferência roda em seguida se o backup terminar com código 0; o Agendador não abre o comando na pasta do script, então use caminhos completos:

```bat
schtasks /Create /TN "SyncSSD" /SC MINUTE /MO 30 /TR "cmd /c \"\"C:\Python312\pythonw.exe\" \"C:\Ferramentas\sync_ssd\programa\sync_ssd.py\" --quarentena --silencioso --alertar-se-velho 7 && \"C:\Python312\pythonw.exe\" \"C:\Ferramentas\sync_ssd\programa\conferir_ssd.py\" --silencioso\""
```

Com `--alertar-se-velho 7`, se você ficar uma semana sem plugar o SSD aparece uma notificação, no máximo uma por dia.

O agendamento segue a escolha de limpeza: com o padrão (`automatico`) ele apaga o que venceu; com `perguntar` ele só avisa (veja [Limpeza do que está guardado](#limpeza-do-que-está-guardado)).

Para conferir se as notificações aparecem na sua tela, rode `python sync_ssd.py --testar-notificacao`.

Uma vez por mês vale rodar `python sync_ssd.py --quarentena --verificar` e olhar o resultado.

## Perguntas frequentes

**A letra do SSD mudou (era D:, virou E:).** Nada a fazer. O script varre todas as letras e reconhece o disco pelo serial e pela identidade.

**Formatei o SSD.** O serial muda na formatação. Rode `--registrar E: --forcar` para registrar de novo; o `.txt` de identidade também precisa ser recriado, e o `--registrar` faz isso.

**Quero usar dois SSDs alternados.** Cada `sync_ssd_config.json` aponta para um disco. Mantenha dois arquivos e chame com `--config`.

**Apaguei um arquivo do Drive por engano.** Se o backup rodou com `--quarentena` depois disso, ele está em `_sync_ssd\quarentena\<data>\` no SSD, na mesma pasta em que estava. Copie de volta. Se o backup ainda não rodou, ele continua no espelho.

**Editei um arquivo e quero a versão de antes.** Está em `_sync_ssd\versoes-antigas\<data>\`, onde a data é a do backup que guardou a versão. Há uma pasta por backup; procure pela mais recente que tenha o arquivo.

**As notificações não aparecem.** Rode `python sync_ssd.py --testar-notificacao`. Se a mensagem de teste não surgir no canto da tela, abra a central de notificações (Windows + N): se ela estiver lá, o "Não incomodar" está ligado e o Windows guarda os avisos sem mostrá-los. Desligue-o, ou em *Configurações → Sistema → Notificações* permita que o Windows PowerShell (o remetente dos avisos) notifique mesmo no "Não incomodar".

**O Drive está em modo "stream" e o backup pulou milhares de arquivos.** No Google Drive para Desktop, clique com o botão direito na pasta e marque "Disponível offline", ou troque para o modo "espelhar". `--baixar` força a cópia, mas vai baixar tudo.

**Renomeei uma pasta grande e a trava disparou.** Isso não deve acontecer: movidos são detectados (mesmo tamanho, mesma data, conteúdo conferido no início e no fim do arquivo) e não contam para a trava. Se disparar mesmo assim, rode `--simular` para ver o motivo e `--forcar` se estiver tudo certo.

**Posso usar com OneDrive, Dropbox ou uma pasta qualquer?** Sim. Só mude `origem`. Os padrões `.gdoc` etc. em `excluir` não atrapalham.

**Roda no macOS ou Linux?** A lógica sim, e os testes rodam lá. Mas `--registrar`, a localização do SSD e a notificação usam a API do Windows. Para outros sistemas seria preciso trocar `info_volume` e `unidades` (UUID do volume via `diskutil`/`blkid`).

## Testes

Dentro da pasta `programa`:

```bash
python -m unittest -v
```

Só biblioteca padrão.

- `test_sync_ssd.py`: varredura, comparação, detecção de movidos, cópia atômica, versões antigas, quarentena, prazos independentes, migração da pasta `versoes`, inventário, trava, espaço, lock, histórico, os quatro modos de saída (tela sem cor fora do terminal, `--detalhado`, `--silencioso` com notificação só em problema, `--json` também nos erros) e a limpeza (as três escolhas, a pergunta S/N, o aviso diário no agendamento, `--limpar`, o menu de configuração).
- `test_conferir_ssd.py`: cada tipo de divergência, exclusões, códigos de saída, os modos de saída, o registro no histórico, garante que a conferência não altera o espelho, e roda backup + conferência em sequência exigindo que concordem.
- `test_visual_ssd.py`: formatação em português, agrupamento por pasta, fallback sem cor e sem símbolos, limite e escape do histórico.

A parte que fala com o Windows (serial do volume, notificação) é substituída por um stub.

## Limitações conhecidas

- Datas e tamanhos iguais são tratados como "mesmo arquivo", pelo backup e pela conferência. Use `--verificar` de vez em quando para pegar corrupção silenciosa.
- Arquivos abertos com bloqueio exclusivo (por exemplo `.pst` do Outlook) falham na cópia e aparecem em "Não copiados". São copiados na próxima execução.
- Dois arquivos na origem que diferem só por maiúsculas/minúsculas colidem no Windows. O Drive permite isso; o script copia o último que encontrar.
- A barra de progresso avança arquivo a arquivo; um vídeo muito grande deixa a barra parada até terminar de copiar.
