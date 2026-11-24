# Curiosity AI Studio Free

Aplicativo desktop em português brasileiro para produzir vídeos de curiosidades de 70 segundos. Usa IA local, pesquisa na Wikipédia e exporta MP4 vertical de 1080 × 1920 a 30 FPS, um SRT e um arquivo de créditos e publicação.

## Instalação no Windows

1. Clone este repositório ou extraia o ZIP em uma pasta gravável. Abra a pasta do projeto.
2. Execute `setup.bat`. Ele usa Python 3.12 para criar `.venv`, instala as dependências Python e baixa a voz local do Piper e o modelo `qwen2.5:3b` no Ollama. Se faltarem Python, FFmpeg ou Ollama, o instalador tenta obtê-los pelo Winget.
3. Execute `iniciar.bat`. Caso o painel indique que o Ollama está inacessível, abra o aplicativo Ollama ou rode `ollama serve` em um terminal.

Se o Winget estiver indisponível, instale [Python 3.12 com o launcher](https://www.python.org/downloads/), [Ollama](https://ollama.com/download) e [FFmpeg](https://ffmpeg.org/download.html) com `ffmpeg` e `ffprobe` no PATH. Depois execute `setup.bat`. Use uma distribuição do FFmpeg compilada com `libx264`.

## Instalação no Linux ou macOS

Com Python 3.12, Ollama, FFmpeg e FFprobe instalados, deixe o Ollama em execução e rode:

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

- Selecione uma categoria ou digite um assunto. **Gerar vídeo automaticamente** executa o fluxo completo. **Criar roteiro para revisar** pausa após a pesquisa e a escrita.
- O editor usa uma linha por cena e exibe a contagem de palavras e a duração estimada da fala. A geração mira 160 palavras em 14 cenas, com faixa padrão de 145 a 175, até 12 no gancho e até 18 nas demais cenas. A opção **Roteiro flexível** em Configurações amplia a faixa total para 130 a 190 palavras. Compare a narração, o título e a descrição com as fontes e os trechos registrados antes de publicar. O aplicativo valida que os trechos existem na Wikipédia. A interpretação e eventuais ajustes manuais ainda exigem revisão editorial.
- Se o resultado ficar fora da faixa ou apresentar outro problema de validação, o Ollama recebe a contagem medida e pode fazer até três correções. Para um gancho longo, a correção pede só a primeira cena. Se ainda houver problemas, o rascunho fica salvo no editor. Confira as fontes, complete as 14 cenas, encurte o gancho se necessário e escolha **Aprovar roteiro revisado**. Depois retome a produção no Dashboard. A aprovação manual permite fugir da faixa de palavras. Os motivos das falhas ficam em `validacao.log`, na pasta local de dados do aplicativo.
- Na galeria, selecione uma cena para substituir a imagem e informe autoria e permissão de uso. Os prompts visuais e links aparecem na dica da cena.
- **Produzir ou retomar projeto** usa as fontes, imagens e narrações já salvas. Se editar uma cena, a narração correspondente será refeita. **Interromper e salvar** encerra entre operações, e a renderização FFmpeg pode ser interrompida durante uma cena. Uma requisição em andamento pode levar até o tempo limite para devolver controle.
- Ao exportar, publique os créditos junto ao vídeo. O TXT traz título, descrição, hashtags, URLs das fontes, autoria, licença e URL de cada imagem. Revise a relevância dos visuais e as condições de publicação. O Commons é filtrado para domínio público, CC0 ou CC BY, com créditos quando aplicáveis. Quando nenhum visual é encontrado, o vídeo usa um cartão gráfico.
- Cada cena ocupa cinco segundos. O FFprobe mede o áudio de cada cena. O FFmpeg ajusta a velocidade da fala preservando o tom e acrescenta silêncio quando necessário para completar a cena sem cortar palavras. A legenda acompanha o intervalo falado e o FFprobe verifica os 70 segundos da faixa de áudio exportada. As legendas ficam visíveis no vídeo e também são entregues em SRT.

## Configurações

- **Ollama** usa `qwen2.5:3b` por padrão. Para usar outro modelo, baixe-o com `ollama pull NOME` e ajuste o nome nas configurações. O modo leve força o Ollama a usar CPU, desativa o ComfyUI e acelera a codificação.
- **Imagens** usam Wikimedia Commons sem conta. Pexels e Pixabay são opcionais e aceitam chaves dos respectivos planos gratuitos, sujeitos a cotas. Configure pela interface ou por `.env`.
- **Narração** usa a voz pt-BR Faber pelo Piper local por padrão. Para outra voz Piper, informe o caminho do modelo nas Configurações e mantenha o `.onnx` e seu `.onnx.json` juntos. O campo de executável aceita um Piper externo. As vozes Francisca e Antônio usam Edge TTS pela internet.
- **ComfyUI** é opcional. Inicie-o com um checkpoint autorizado para seu uso. Exporte um fluxo Stable Diffusion no formato API JSON, substitua o texto do nó de prompt positivo por `{{PROMPT}}` e termine o fluxo em `SaveImage`. Informe o caminho do JSON, habilite o ComfyUI e desative o modo leve. Se a geração falhar, o aplicativo tenta buscar na internet.
- **Histórico** fica na pasta de dados local do sistema, separado da instalação. Os temas automáticos usam uma seleção por categoria. O histórico compara o tema normalizado e a página principal da Wikipédia para evitar repetir esses projetos. Assuntos diferentes que descrevem o mesmo fato em páginas diferentes ainda podem coincidir.

Ollama, pesquisa e mídia funcionam sem assinatura da OpenAI e sem APIs pagas. Wikipédia, bancos de imagens e Edge TTS precisam de internet. Piper e ComfyUI usam modelos baixados separadamente.

## Testes

```sh
python -m unittest discover -s tests -v
CURIOSITY_TEST_RENDER=1 python -m unittest discover -s tests -v
```

No Prompt de Comando do Windows, use `set CURIOSITY_TEST_RENDER=1` antes do segundo comando. O teste completo usa o FFmpeg real e confirma os fluxos de vídeo, áudio e legenda.
