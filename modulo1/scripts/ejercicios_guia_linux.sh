#!/usr/bin/env bash
# Ejercicios Linux de la guía del curso (M1A2, Guía 2, Prof. Oscar Mondragón), en versión compacta.
# Cada comando se imprime antes de ejecutarse, con «$ » delante, como en la guía.
# Uso, desde Windows:
#   vagrant ssh servidor -c "bash /sitecali-repo/modulo1/scripts/ejercicios_guia_linux.sh 1"   (ejercicios 1 a 7)
#   vagrant ssh servidor -c "bash /sitecali-repo/modulo1/scripts/ejercicios_guia_linux.sh 2"   (ejercicios 8 a 13)
# Trabaja en ~/temp del usuario vagrant y lo borra al terminar la parte 2. No toca /srv/sitecali.
set -e
PS4='$ '
parte="${1:-1}"
cd ~
if [ "$parte" = "1" ]; then
  rm -rf ~/temp
  set -x
  pwd
  mkdir temp
  mkdir -p temp/stuff/things/orange/apple/pear/grape
  cd temp/stuff/things/orange/apple/pear/grape
  pwd
  cd ../../..
  pwd
  cd ~/temp
  ls
  ls stuff/things/orange
  rmdir stuff/things/orange/apple/pear/grape
  rmdir stuff/things/orange/apple/pear
  rmdir -p stuff/things/orange/apple
  ls
  mkdir -p i/like/icecream
  pushd i/like/icecream
  pushd ~/temp/i/like
  popd
  popd
  pwd
  { set +x; } 2>/dev/null
else
  mkdir -p ~/temp
  cd ~/temp
  rm -rf ~/temp/*
  set -x
  touch iamcool.txt
  cp iamcool.txt neat.txt
  mkdir something
  cp neat.txt something/
  cp -r something newplace
  mv neat.txt uncool.txt
  mv newplace oldplace
  ls -F
  echo "I am a fun guy." | tee test.txt
  echo "Because I make poems," | tee -a test.txt
  cat test.txt
  more test.txt
  rm uncool.txt iamcool.txt test.txt
  rm something/neat.txt
  rmdir something
  rm -rf oldplace
  ls -A
  cd ~
  rm -r temp
  { set +x; } 2>/dev/null
fi
