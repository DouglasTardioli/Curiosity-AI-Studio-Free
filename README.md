# Curiosity AI Studio Free

Aplicativo desktop em português brasileiro para produzir vídeos de curiosidades de 70 segundos. Usa IA local, pesquisa na Wikipédia e exporta MP4 vertical de 1080 × 1920 a 30 FPS, legendas SRT e ASS e um arquivo de créditos e publicação.

## Instalação no Windows

1. Clone este repositório ou extraia o ZIP em uma pasta gravável. Abra a pasta do projeto.
2. Execute `setup.bat`. Ele usa Python 3.12 para criar `.venv`, instala as dependências Python e baixa a voz local do Piper e o modelo `qwen2.5:3b` no Ollama. Se faltarem Python, FFmpeg ou Ollama, o instalador tenta obtê-los pelo Winget.
3. Execute `iniciar.bat`. Caso o painel indique que o Ollama está inacessível, abra o aplicativo Ollama ou rode `ollama serve` em um terminal.

Se o Winget estiver indisponível, instale [Python 3.12 com o launcher](https://www.python.org/downloads/), [Ollama](https://ollama.com/download) e [FFmpeg](https://ffmpeg.org/download.html) com `ffmpeg` e `ffprobe` no PATH. Depois execute `setup.bat`. Use uma distribuição do FFmpeg compilada com `libx264` e `libass`, com os filtros `ass`, `xfade`, `overlay` e `sidechaincompress`. Confira a lista com `ffmpeg -filters`.

## Instalação no Linux ou macOS

Com Python 3.12, Ollama, FFmpeg com libass e FFprobe instalados, deixe o Ollama em execução e rode:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp -n .env.example .env
mkdir -p vozes
.venv/bin/python -m piper.download_voices --download-dir vozes pt_BR-faber-medium
ollama pull qwen2.5:3b
.venv/bin/python -m studio
```

## Produção

1. Escolha o tema, a voz, o modo de edição e a intensidade. **Gerar e renderizar vídeo** executa todo o fluxo. **Criar roteiro para revisar** abre o editor antes do processamento de mídia. Com **Renderizar após aprovar o roteiro** ativo, aprovar um rascunho retoma automaticamente a produção.
2. O editor mantém 14 cenas e mostra contagem e duração estimada. A faixa padrão é de 145 a 175 palavras, e **Roteiro flexível** permite 130 a 190. O Ollama recebe a contagem do Python e pode fazer até três correções. Confira as fontes e os trechos das cenas antes de publicar. Os problemas ficam em `validacao.log` na pasta de dados do aplicativo.
3. A galeria é opcional. Use **Preparar cenas para revisão opcional**, **Buscar outra imagem** ou **Selecionar imagem local**. O seletor local importa o arquivo sem pedir autoria ou licença. Use arquivos locais que você tenha autorização para reutilizar. A prévia da primeira cena abre no player padrão em 540 × 960 a 30 FPS.
4. Exporte o MP4 vertical com 70 s, 1080 × 1920, 30 FPS, H.264 e AAC. A exportação também inclui legendas SRT e ASS, fontes e créditos de mídia. As informações de autoria e licença ficam nos créditos exportados.

### Roteiro manual

No Dashboard, selecione **Inserir roteiro manualmente**. Digite, cole ou importe um TXT em UTF-8. Use 14 linhas, uma por cena, com 145 a 175 palavras no total. O gancho aceita até 12 palavras, e cada cena seguinte aceita até 18. O editor atualiza os contadores e realça as linhas inválidas.

**Salvar roteiro** guarda o rascunho no Histórico, inclusive antes de atender aos limites. **Aprovar e gerar vídeo** valida o texto e inicia a produção das 14 cenas de 5 segundos. O Ollama é usado apenas para consultas e planejamento visual. A narração e as legendas usam o texto aprovado, e o aplicativo registra os créditos disponíveis automaticamente.

## Edição e mídia

- **Leve** aplica movimentos de câmera suaves, crossfade e poucos elementos. **Dinâmico** acrescenta gráficos animados, impactos, transições e som. **Cinematográfico** usa movimentos em camadas, partículas geradas e desfoque. **Viral** alterna zoom de impacto, tremilique, salto e pop-up. Ajuste a intensidade de 0% a 100%. Em 0%, os efeitos opcionais ficam desativados.
- Cada cena dura cinco segundos. O Ollama pode planejar câmera, mídia, palavra de destaque, PNG, entrada, posição, escala, rotação, saída e som. O aplicativo valida valores e nomes de efeitos e mantém um plano determinístico como fallback. Quando existe vídeo de apoio pertinente, ele entra no ponto de mudança visual da cena. As transições usam a parte final da cena e preservam todos os 2.100 quadros.
- O aplicativo pesquisa imagens conforme as fontes disponíveis e prioriza mídias de domínio público, CC0 ou com licença comercial compatível entre os resultados relevantes. Os provedores de PNG incluem Commons, Openverse, Pixabay e Openclipart quando disponíveis. A busca valida metadados, formato e transparência real. Se faltar uma mídia adequada, o aplicativo gera uma peça em Pillow para continuar automaticamente.
- Uma pasta de mídia local pode conter PNGs e clipes `.mp4`, `.mov` ou `.webm`. Use nomes descritivos em inglês para combinar com as palavras-chave. Vídeos licenciados também podem ser obtidos no Pixabay quando a chave gratuita está configurada. PNGs, imagens, narrações, efeitos sonoros e cenas renderizadas são reaproveitados localmente.
- O aplicativo sintetiza efeitos CC0, como whoosh, impacto, pop e suspense. Você pode indicar uma faixa musical local. O FFmpeg faz ducking de música e efeitos conforme a narração. Arquivos de música importados ficam identificados nos créditos.
- O FFprobe verifica as mídias de entrada, as cenas renderizadas e o MP4 final. O vídeo final usa 1080 × 1920, 30 FPS constantes, pixels quadrados e yuv420p. Se um efeito falhar, o aplicativo tenta uma versão simplificada e, se necessário, renderiza a cena com zoom suave. O arquivo `validacao.log` registra a cena, o efeito, os formatos de entrada, o comando e os erros do FFmpeg.
- As legendas usam DejaVu Sans Bold, contorno preto e até duas linhas na área segura. Nos modos animados, a palavra importante ganha destaque amarelo. O Edge TTS pode fornecer tempos por palavra. Com Piper ou quando esses tempos estiverem indisponíveis, a duração de cada bloco vem do áudio e a posição da palavra dentro do bloco é estimada. SRT e ASS são UTF-8.

## Configurações

- **Ollama** usa `qwen2.5:3b` por padrão. Para usar outro modelo, baixe-o com `ollama pull NOME` e ajuste o nome nas configurações. O modo leve força o Ollama a usar CPU, desativa o ComfyUI e acelera a codificação.
- **Imagens** usam Wikimedia Commons sem conta. Pexels e Pixabay são opcionais e aceitam chaves dos respectivos planos gratuitos, sujeitos a cotas. Configure pela interface ou por `.env`. O cache local reutiliza as fotos baixadas, e a busca limita os downloads simultâneos a três.
- **Narração** usa a voz pt-BR Faber pelo Piper local por padrão. Para outra voz Piper, informe o caminho do modelo nas Configurações e mantenha o `.onnx` e seu `.onnx.json` juntos. O campo de executável aceita um Piper externo. As vozes Francisca e Antônio usam Edge TTS pela internet.
- **ComfyUI** é opcional. Inicie-o com um checkpoint autorizado para seu uso. Exporte um fluxo Stable Diffusion no formato API JSON, substitua o texto do nó de prompt positivo por `{{PROMPT}}` e termine o fluxo em `SaveImage`. Informe o caminho do JSON, habilite o ComfyUI e desative o modo leve. Se a geração falhar, o aplicativo tenta buscar na internet.
- **Histórico** fica na pasta de dados local do sistema, separado da instalação. Os temas automáticos usam uma seleção por categoria. O histórico compara o tema normalizado e a página principal da Wikipédia para evitar repetir esses projetos. Assuntos diferentes que descrevem o mesmo fato em páginas diferentes ainda podem coincidir.

Ollama, pesquisa e mídia funcionam sem assinatura da OpenAI e sem APIs pagas. Wikipédia, bancos de imagens e Edge TTS precisam de internet. Piper e ComfyUI usam modelos baixados separadamente.

## Testes

```sh
python -m unittest discover -s tests -v
CURIOSITY_TEST_RENDER=1 python -m unittest discover -s tests -v
```

No Prompt de Comando do Windows, use `set CURIOSITY_TEST_RENDER=1` antes do segundo comando. A suíte de renderização usa o FFmpeg real para testar os efeitos, as transições e os fluxos de vídeo, áudio e legenda. Também valida um MP4 de 70 segundos com 14 cenas.

Para reproduzir a prévia animada no modo Viral, escolha um roteiro com Piper, selecione intensidade de 80% e clique em **Prévia de 5 s** na galeria.
