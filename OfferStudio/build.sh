#!/bin/zsh
set -eu
cd "$(dirname "$0")"
mkdir -p OfferStudio.app/Contents/MacOS
cp Info.plist OfferStudio.app/Contents/Info.plist
xcrun swiftc Sources/*.swift -o OfferStudio.app/Contents/MacOS/OfferStudio -framework SwiftUI -framework WebKit -framework AppKit -O
codesign --force --deep --sign - OfferStudio.app
