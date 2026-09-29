# sync_ssd

Backup de mão única da pasta local do **Google Drive para Desktop** (ou de qualquer pasta do Windows) para **um SSD externo específico**, em um único arquivo Python sem dependências.

A ideia é simples: você pluga o SSD, o script reconhece que é *aquele* SSD, copia o que mudou e guarda a versão anterior de cada arquivo sobrescrito. Ele nunca apaga nada e se recusa a rodar quando algo parece errado.

> **English summary.** One-way backup from a local Google Drive folder to one specific external SSD, identified by volume serial number plus an identity file. Never deletes, keeps previous versions for a configurable period, detects moved/renamed files, refuses to run if too many files change at once (ransomware guard), checks free space, and skips files Google Drive has not downloaded yet. Windows only, Python 3.8+, no third-party packages. The rest of this README is in Portuguese.

---

## O que ele garante

| Garantia | Como |
|---|---|
| Só grava no SSD certo | Exige o **número de série do volume** e um **arquivo de identidade** com um ID gerado no registro. Outro disco com a mesma letra ou o mesmo nome é recusado. |
| Nunca apaga | Arquivos que saíram do Drive ficam no SSD como "órfãos". Só saem do lugar se você pedir com `--quarentena`, e mesmo assim vão para a pasta de versões, não para o lixo. |
| Nunca deixa arquivo pela metade | Copia para um `.sync_tmp` e só então troca pelo definitivo. |
| Guarda a versão anterior | Antes de sobrescrever, move o arquivo antigo para `_sync_ssd/versoes/<data-hora>/`. Versões ficam por 90 dias (configurável). |
| Não duplica ao mover pasta | Pasta renomeada ou movida no Drive é movida dentro do SSD, sem recopiar. |
| Trava contra ransomware | Se muitos arquivos mudarem de uma vez, aborta sem copiar nada. |
| Não estoura o disco | Confere o espaço livre antes de começar. |
| Não roda duas vezes ao mesmo tempo | Lock file local. |
| Não força download do Drive | Arquivos que o Drive ainda não baixou (modo *stream*) são pulados com aviso. |

## Requisitos

- Windows 10 ou 11.
- Python 3.8 ou mais novo (a lógica também roda em macOS/Linux, mas o reconhecimento do SSD por número de série usa a API do Windows).
- Nenhum pacote externo.
- Recomendado: SSD formatado em **exFAT** ou **NTFS**. FAT32 não aceita arquivos acima de 4 GB.

## Instalação (para quem fez fork)

1. Clone ou baixe o repositório em uma pasta fixa, por exemplo `C:\Ferramentas\sync_ssd\`.
2. Copie `sync_ssd_config.example.json` para `sync_ssd_config.json` e ajuste pelo menos `origem`. Ou pule esta etapa e deixe o `--registrar` criar o arquivo com os padrões.
3. Plugue o SSD e registre-o (troque `D:` pela letra dele):

   ```bat
   python sync_ssd.py --registrar D:
   ```

   Isso grava `D:\_sync_ssd\IDENTIDADE_SSD.txt` no SSD e salva serial + id em `sync_ssd_config.json`.

4. Veja o que o primeiro backup faria:

   ```bat
   python sync_ssd.py --simular
   ```

5. Rode o primeiro backup. Como o SSD está vazio, 100% dos arquivos são "novos" e a trava de segurança dispara. Isso é esperado. Passe `--forcar` só desta vez:

   ```bat
   python sync_ssd.py --forcar
   ```

6. A partir daí, `python sync_ssd.py` sem argumentos faz o backup incremental.

## Uso

```
python sync_ssd.py                       backup incremental
python sync_ssd.py --simular             mostra o que faria, sem tocar em nada
python sync_ssd.py --forcar              ignora a trava de segurança
python sync_ssd.py --orfaos              só lista o que está no SSD e saiu do Drive (não copia nada)
python sync_ssd.py --quarentena          backup + move órfãos para _sync_ssd/versoes/<data>/
python sync_ssd.py --verificar [N]       backup + confere o SHA-256 de N arquivos (padrão 200; 0 = todos)
python sync_ssd.py --baixar              copia também arquivos que o Drive ainda não baixou
python sync_ssd.py --status              resultado da última execução
python sync_ssd.py --alertar-se-velho 7  se o SSD não estiver plugado e o último backup tiver
                                         mais de 7 dias, mostra um aviso na tela (no máximo 1x/dia)
python sync_ssd.py --config ARQUIVO      usa outro sync_ssd_config.json
python sync_ssd.py --registrar D:        registra o SSD (com --forcar para trocar de disco)
```

Os flags combinam: `python sync_ssd.py --quarentena --verificar 500` faz backup, quarentena e verificação.

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

## Configuração

O arquivo `sync_ssd_config.json` fica ao lado do script. Se não existir ali, o script procura em `%LOCALAPPDATA%\SyncSSD\`. Qualquer chave omitida usa o valor padrão.

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
| `dias_versoes` | `90` | Por quantos dias versões antigas ficam guardadas. |
| `max_versoes_gb` | `0` | Teto de espaço para a pasta de versões. Ao passar, as mais antigas saem primeiro. `0` = sem teto. |
| `margem_espaco_gb` | `2` | Espaço que deve sobrar livre no SSD depois do backup. |
| `caminhos_longos` | `true` | Usa o prefixo `\\?\` para aceitar caminhos com mais de 260 caracteres. Desligue se algum caminho de rede se comportar mal. |
| `horas_lock_velho` | `12` | Um lock mais velho que isso é considerado abandonado (queda de energia, por exemplo). |

### O que é público e o que não é

`serial` e `id` não são senhas, mas juntos são exatamente o que identifica o seu SSD para o script. Se o repositório for público, prefira não versionar o `sync_ssd_config.json` real. O `.gitignore` deste repositório já o ignora; versione o `sync_ssd_config.example.json` no lugar. Os logs e o `ultimo_resultado.json` contêm nomes de arquivos do seu Drive e também estão no `.gitignore`.

## O que fica no SSD

```
D:\
├── (espelho do Drive, ou dentro de pasta_destino)
└── _sync_ssd\
    ├── IDENTIDADE_SSD.txt        id + serial + data do registro. Não apague.
    ├── ultimo_resultado.json     resumo da última execução
    ├── logs\2026-09.log          um arquivo por mês
    └── versoes\
        └── 2026-09-28_183000\    uma pasta por execução que sobrescreveu ou quarentenou algo,
            └── Projetos\...      com a mesma estrutura de pastas do espelho
```

O espelho são arquivos comuns. **Para restaurar, basta copiar de volta** com o Explorer. Não há formato proprietário nem banco de dados.

No computador, em `%LOCALAPPDATA%\SyncSSD\`, ficam o log local, o `ultimo_resultado.json` (para `--status` e `--alertar-se-velho` funcionarem sem o SSD plugado) e o lock.

## Agendamento

A forma mais simples é rodar a cada 30 minutos. Quando o SSD não está plugado o script sai em menos de um segundo com código 2, então o custo é zero. Use `pythonw.exe` para não abrir janela de console.

```bat
schtasks /Create /TN "SyncSSD" /SC MINUTE /MO 30 /TR "\"C:\Python312\pythonw.exe\" \"C:\Ferramentas\sync_ssd\sync_ssd.py\" --alertar-se-velho 7"
```

Com `--alertar-se-velho 7`, se você ficar uma semana sem plugar o SSD aparece uma caixa de aviso, no máximo uma por dia.

Uma vez por mês vale rodar manualmente `python sync_ssd.py --quarentena --verificar` e olhar o log.

## Perguntas frequentes

**A letra do SSD mudou (era D:, virou E:).** Nada a fazer. O script varre todas as letras e reconhece o disco pelo serial e pela identidade.

**Formatei o SSD.** O serial muda na formatação. Rode `--registrar E: --forcar` para registrar de novo; o `.txt` de identidade também precisa ser recriado, e o `--registrar` faz isso.

**Quero usar dois SSDs alternados.** Cada `sync_ssd_config.json` aponta para um disco. Mantenha dois arquivos e chame com `--config`.

**O Drive está em modo "stream" e o backup pulou milhares de arquivos.** No Google Drive para Desktop, clique com o botão direito na pasta e marque "Disponível offline", ou troque para o modo "espelhar". `--baixar` força a cópia, mas vai baixar tudo.

**Renomeei uma pasta grande e a trava disparou.** Na versão atual isso não deve acontecer: movidos são detectados (mesmo tamanho, mesma data, conteúdo conferido no início e no fim do arquivo) e não contam para a trava. Se disparar mesmo assim, rode `--simular` para ver o motivo e `--forcar` se estiver tudo certo.

**Posso usar com OneDrive, Dropbox ou uma pasta qualquer?** Sim. Só mude `origem`. Os padrões `.gdoc` etc. em `excluir` não atrapalham.

**Roda no macOS ou Linux?** A lógica sim, e os testes rodam lá. Mas `--registrar` e a localização do SSD usam a API do Windows. Para outros sistemas seria preciso trocar `info_volume` e `unidades` (UUID do volume via `diskutil`/`blkid`).

## Testes

```bash
python -m unittest -v
```

Só biblioteca padrão. Os testes cobrem varredura, comparação, detecção de movidos, cópia atômica, versionamento, quarentena, trava, espaço, lock e o fluxo completo em pastas temporárias. A parte que fala com o Windows é substituída por um stub.

## Limitações conhecidas

- Datas e tamanhos iguais são tratados como "mesmo arquivo". Use `--verificar` de vez em quando para pegar corrupção silenciosa.
- Arquivos abertos com bloqueio exclusivo (por exemplo `.pst` do Outlook) falham na cópia e aparecem como erro no log. São copiados na próxima execução.
- Dois arquivos na origem que diferem só por maiúsculas/minúsculas colidem no Windows. O Drive permite isso; o script copia o último que encontrar.
